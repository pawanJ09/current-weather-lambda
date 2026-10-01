# current-weather-lambda

Deterministic **tool** in the Agentic Weather App (Style 1: Amazon Bedrock
Agents). A plain Lambda function that fetches current weather conditions
from [Open-Meteo](https://open-meteo.com/) for a given latitude/longitude.
No AI, no reasoning, no loop — it's called by the `weather-orchestrator-agent`
as an Action Group backend.

Runtime: **Python 3.14** · Dependencies: **none** (standard library only)

## Why no `requests` library

The architecture diagrams describe "Python + requests," but this handler
uses `urllib` from the standard library instead. Functionally identical for
a single GET call, and it means the deployment package is just `handler.py`
zipped up — no dependency layer, no `pip install -t`, no vendoring step in
CI. If you'd rather use `requests`, add it to `requirements.txt` and update
the deploy workflow's packaging step to `pip install -r requirements.txt -t
build/` before zipping.

## Repo layout

```
src/handler.py          Lambda handler + Open-Meteo client
tests/test_handler.py   Unit tests (mocked HTTP, no real network calls)
events/                 Sample invocation payloads (direct + Bedrock shape)
schema/                 OpenAPI schema for the future Action Group
iam/                    Trust policy, execution policy, Bedrock invoke permission
.github/workflows/      CI (lint+test) and Deploy (build, deploy, smoke-test)
```

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check src tests          # lint
ruff format src tests         # format
pytest --cov=src              # unit tests
```

## Manual test (no AWS needed)

```bash
python3 -c "
from src.handler import lambda_handler
import json
print(lambda_handler(json.load(open('events/sample_event_direct.json')), None))
"
```

This makes a real call to Open-Meteo (no API key required) and prints the
parsed weather for New York City.

## AWS setup: fully automatic, first run included

The deploy workflow bootstraps everything itself — there's no manual
`aws iam create-role` / `aws lambda create-function` step to run by hand.
On the **first** push to `main`, the `deploy` job:

1. Checks whether `current-weather-lambda-role` exists (`aws iam get-role`).
   If not, creates it from `iam/trust-policy.json` and attaches
   `iam/execution-role-policy.json`, then pauses ~10s for IAM propagation.
2. Checks whether the `current-weather-lambda` function exists
   (`aws lambda get-function`). If not, calls `create-function` with that
   role's ARN — retrying a few times with backoff, since a brand-new IAM
   role can take a little longer than 10s to become assumable by Lambda.

On every **subsequent** push, both of those checks find existing resources
and the job just calls `update-function-code` instead. Same workflow, same
one command (`git push`), correct behavior either way — nothing to run
manually except the one-time OIDC setup below (which can't bootstrap
itself, since it's what grants the workflow AWS access in the first place).

Once `weather-orchestrator-agent` exists and you attach this function as an
Action Group backend, the console/CLI step that attaches it typically adds
the required `bedrock.amazonaws.com` invoke permission automatically. If you
ever need to add it by hand, see `iam/bedrock-invoke-permission.json` for
the exact `aws lambda add-permission` command.

## GitHub Actions setup

Two workflows:

- **`ci.yml`** — runs on every PR and on pushes to any non-`main` branch.
  Lints with `ruff`, then runs `pytest` with an enforced 90% coverage floor.
  It's also a reusable workflow (`workflow_call`) so `deploy.yml` runs the
  exact same checks before it deploys.
- **`deploy.yml`** — runs on push to `main` (or manually via
  `workflow_dispatch`). Three jobs in sequence:
  1. `ci` — reruns lint + tests as a gate
  2. `deploy` — zips `src/handler.py`, creates the execution role and the
     function if they don't exist yet (first run), or updates the
     function's code if they do (every run after) — see previous section
  3. `validate` — invokes the **live deployed function** with
     `events/sample_event_direct.json` and fails the workflow if the
     response isn't a 200 with a `temperature_f` field

### Required repo secret

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | ARN of an IAM role GitHub can assume via OIDC |

This is the one thing that genuinely can't be automated by the workflow
itself — something has to grant GitHub Actions AWS access in the first
place. Uses OIDC (`aws-actions/configure-aws-credentials`) rather than
long-lived access keys. One-time setup:

**1. OIDC identity provider** (skip if another repo in this account already
set one up for `token.actions.githubusercontent.com`):

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

**2. A deploy role trusting your repo**, with a permissions policy covering
everything the workflow now does — role bootstrapping *and* Lambda
create/update/invoke. Note `iam:PassRole`: it's easy to miss and
`create-function` fails with an opaque access-denied without it, since
AWS requires explicit permission to hand a role to another service.

Trust policy:
```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": { "token.actions.githubusercontent.com:sub": "repo:<YOUR_GH_ORG>/current-weather-lambda:ref:refs/heads/main" }
  }
}
```

Permissions policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RoleBootstrap",
      "Effect": "Allow",
      "Action": ["iam:GetRole", "iam:CreateRole", "iam:PutRolePolicy"],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/current-weather-lambda-role"
    },
    {
      "Sid": "PassRoleToLambda",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/current-weather-lambda-role",
      "Condition": { "StringEquals": { "iam:PassedToService": "lambda.amazonaws.com" } }
    },
    {
      "Sid": "LambdaDeployAndInvoke",
      "Effect": "Allow",
      "Action": [
        "lambda:GetFunction",
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:current-weather-lambda"
    }
  ]
}
```

## Next component

Once this is green, move on to `forecast-weather-lambda` — structurally
identical, just a different Open-Meteo endpoint and response shape.