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

## First-time AWS setup (one-time, manual)

The GitHub Actions deploy workflow **updates code on an existing function**
— it doesn't create the function or its role the first time. Do that once:

```bash
# 1. Create the execution role
aws iam create-role \
  --role-name current-weather-lambda-role \
  --assume-role-policy-document file://iam/trust-policy.json

aws iam put-role-policy \
  --role-name current-weather-lambda-role \
  --policy-name current-weather-lambda-logs \
  --policy-document file://iam/execution-role-policy.json

# 2. Package and create the function
mkdir -p build && cp src/handler.py build/ && (cd build && zip -r ../function.zip .)

aws lambda create-function \
  --function-name current-weather-lambda \
  --runtime python3.14 \
  --handler handler.lambda_handler \
  --role arn:aws:iam::<ACCOUNT_ID>:role/current-weather-lambda-role \
  --zip-file fileb://function.zip \
  --timeout 10 \
  --memory-size 128
```

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
  2. `deploy` — zips `src/handler.py` and calls `aws lambda
     update-function-code`, then waits for the update to finish
  3. `validate` — invokes the **live deployed function** with
     `events/sample_event_direct.json` and fails the workflow if the
     response isn't a 200 with a `temperature_f` field

### Required repo secret

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | ARN of an IAM role GitHub can assume via OIDC, scoped to `lambda:UpdateFunctionCode`, `lambda:GetFunction`, `lambda:InvokeFunction`, and `lambda:UpdateFunctionCode` on this function's ARN |

Uses OIDC (`aws-actions/configure-aws-credentials`) rather than long-lived
access keys. You'll need a one-time IAM OIDC identity provider for
`token.actions.githubusercontent.com` in your account (skip if you already
set this up for another repo) and a role trusting your repo:

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

## Next component

Once this is green, move on to `forecast-weather-lambda` — structurally
identical, just a different Open-Meteo endpoint and response shape.
