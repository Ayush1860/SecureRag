# Deploying the SecureRAG demo on AWS

The React frontend runs on **Amplify Hosting**. The FastAPI backend runs on **Lambda** as a
container image behind a Function URL. Everything is in **ap-south-1**, and every resource is
tagged `project=securerag`.

Budget target: under $1/month. You run every command in this guide; nothing here creates
resources on its own.

| Piece | What | Monthly cost |
|---|---|---|
| `backend.yaml` stack | ECR repo, Lambda function + Function URL, log group, 2 IAM roles | ~$0.20 (ECR storage, 2 images) |
| Lambda compute | 3008 MB, reserved concurrency 2 | ~$0 (always-free 400k GB-s) |
| CloudWatch Logs | 7-day retention | ~$0 |
| SSM Parameter Store | 3 standard SecureStrings, `aws/ssm` key | $0 |
| Amplify Hosting | static React build | ~$0 at demo traffic |
| GitHub Actions | build, deploy, benchmark | $0 (public repo) |

**Never enable:** Amplify WAF ($15/month), Lambda provisioned concurrency, a NAT gateway, or an
always-on EC2 instance.

---

## 0. Prerequisites (once, console, $0)

1. **Budget:** Billing → Budgets → monthly cost budget of $20, with alerts at $2 / $5 / $10
   (actual) and $20 (forecast).
2. **GitHub OIDC provider:** IAM → Identity providers → Add → OpenID Connect,
   URL `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
   **Windows shortcut for steps 3-4:** `powershell -ExecutionPolicy Bypass -File deployws\setup_credentials.ps1`
   logs in to GitHub and AWS, generates the keys and stores every secret in SSM / GitHub itself.
3. **Demo keys** (locally):
   ```bash
   python scripts/create_demo_keys.py --hashed-out data/demo_api_keys.hashed.json > data/demo_keys_plain.json
   ```
   `data/` is git-ignored. The plaintext JSON goes into the GitHub secret `DEMO_KEYS_JSON` in step 3.
4. **SSM parameters** (SecureString, default `aws/ssm` key, $0):
   ```bash
   REGION=ap-south-1
   aws ssm put-parameter --region $REGION --type SecureString --name /securerag/aes_key_b64 \
       --value "$(python scripts/generate_key.py)"          # a NEW key, never your local one
   aws ssm put-parameter --region $REGION --type SecureString --name /securerag/api_keys_json \
       --value file://data/demo_api_keys.hashed.json
   aws ssm put-parameter --region $REGION --type SecureString --name /securerag/groq_api_key \
       --value "<your free Groq key>"                      # optional: without it the app refuses to answer
   aws ssm add-tags-to-resource --region $REGION --resource-type Parameter \
       --resource-id /securerag/aes_key_b64 --tags Key=project,Value=securerag   # repeat per parameter
   ```

## 1. First deploy (two passes, because a container Lambda needs an image to exist first)

**Windows shortcut for all of sections 0-1:** after `setup_credentials.ps1`, run
`powershell -ExecutionPolicy Bypass -File deploywsirst_deploy.ps1`. It pushes `main`, creates the
budget and the OIDC provider, runs both stack passes with the image build on GitHub Actions in between,
sets the GitHub variables and smoke-tests the Function URL. Every step is skipped when already done.

**Pass 1: repository and deploy role** (~2 min, $0)
```bash
aws cloudformation deploy --region ap-south-1 --stack-name securerag \
  --template-file deploy/aws/backend.yaml --capabilities CAPABILITY_IAM \
  --tags project=securerag
aws cloudformation describe-stacks --region ap-south-1 --stack-name securerag --query "Stacks[0].Outputs"
```

**First image push.** Set these GitHub repo variables (Settings → Secrets and variables → Actions
→ Variables) from the outputs:
- `AWS_DEPLOY_ROLE_ARN` = `DeployRoleArn`
- `ECR_REPOSITORY_URI` = `EcrRepositoryUri`
- `LAMBDA_FUNCTION_NAME` = `FunctionName` (`securerag-api`)

The function doesn't exist yet. Either run **Actions → Deploy backend (Lambda)** by hand (without a
function it only builds and pushes the image, then prints the `ImageUri` for pass 2), or, from a
machine with Docker:
```bash
aws ssm get-parameter --region ap-south-1 --name /securerag/aes_key_b64 --with-decryption \
  --query Parameter.Value --output text > /tmp/aes_key
IMAGE=<EcrRepositoryUri>:$(git rev-parse HEAD)
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ${IMAGE%%/*}
docker buildx build --target lambda --platform linux/amd64 --provenance=false --sbom=false \
  --secret id=aes_key,src=/tmp/aes_key -t "$IMAGE" --load . && rm /tmp/aes_key && docker push "$IMAGE"
```
(~$0.02 of ECR storage per image per month.)

**Pass 2: create the function and its URL** (~3 min)
```bash
aws cloudformation deploy --region ap-south-1 --stack-name securerag \
  --template-file deploy/aws/backend.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides ImageUri=$IMAGE AmplifyOrigin=https://localhost.invalid
```
Then set the repo variable `FUNCTION_URL` (output `FunctionUrl`, no trailing slash) and the repo
secret `DEMO_KEYS_JSON` (the contents of `data/demo_keys_plain.json`). Check it end to end:
```bash
SMOKE_KEYS="$(cat data/demo_keys_plain.json)" python scripts/smoke_api.py --base-url <FunctionUrl>
```
The first call triggers a cold start of about 20 s. Look for `INIT_REPORT` in the log group.

## 2. Frontend on Amplify (console, ~$0)

1. Amplify → **Host web app** → GitHub → repo `Ayush1860/SecureRag`, branch `main`. Amplify reads
   `amplify.yml` from the repo root (app root `frontend`).
2. Environment variables:
   - `AMPLIFY_MONOREPO_APP_ROOT=frontend` (required, because `amplify.yml` uses `appRoot: frontend`)
   - `VITE_API_BASE=<FunctionUrl>` (no trailing slash)
   - optional `VITE_DEMO_KEYS_JSON=<contents of data/demo_keys_plain.json>`, which shows one-click
     demo credentials in the "About this demo" panel (see SECURITY.md, "Public demo credentials")
3. **Rewrites and redirects → Manage → add the SPA rewrite:**

   | Source address | Target address | Type |
   |---|---|---|
   | `</^[^.]+$\|\.(?!(css\|gif\|ico\|jpg\|js\|png\|txt\|svg\|woff\|woff2\|ttf\|map\|json\|webp)$)([^.]+$)/>` | `/index.html` | 200 (Rewrite) |

4. `amplify.yml`'s CSP `connect-src` must hold your Function URL origin. It is set for the current
   stack; if you recreate the stack (new URL), update it, commit, and let Amplify redeploy.
5. Put the Amplify domain (e.g. `https://main.d123abc.amplifyapp.com`) into the backend's CORS:
   ```bash
   aws cloudformation deploy --region ap-south-1 --stack-name securerag \
     --template-file deploy/aws/backend.yaml --capabilities CAPABILITY_IAM \
     --parameter-overrides ImageUri=$IMAGE AmplifyOrigin=https://main.d123abc.amplifyapp.com
   ```
6. **Leave WAF off.**

## 3. Redeploy (automatic, $0)

A push to `main` that touches `securerag/`, `app/`, `Dockerfile` or `requirements.txt` runs
`.github/workflows/deploy-backend.yml` (or run it by hand). It:
1. builds the `lambda` target, with the AES key fetched from SSM as a build secret;
2. pushes `<repo>:<git sha>`;
3. updates the function;
4. smoke-tests every demo role;
5. **rolls back to the previous image** if the smoke test fails.

ECR keeps the newest 2 images. After a code redeploy, the CloudFormation `ImageUri` parameter is
stale, and that is harmless. Pass the current image on your next stack update so the stack
doesn't roll the function back.

## 4. Rotate the demo keys ($0)

```bash
python scripts/create_demo_keys.py --hashed-out data/demo_api_keys.hashed.json > data/demo_keys_plain.json
aws ssm put-parameter --region ap-south-1 --name /securerag/api_keys_json --type SecureString \
  --value file://data/demo_api_keys.hashed.json --overwrite
```
Then update the GitHub secret `DEMO_KEYS_JSON` and the keys shown on the Amplify page. Warm
containers keep the old keys until they recycle; to force the change, re-run the deploy workflow
(a new image means new containers).

Rotating the **AES key** means rebuilding the image, because the demo index is encrypted at
build time. Put a new `/securerag/aes_key_b64` and re-run the deploy workflow.

## 5. Teardown (stops all charges)

```bash
# 1. Amplify console: delete the app.
# 2. Stack (EmptyOnDelete removes the ECR images with the repository):
aws cloudformation delete-stack --region ap-south-1 --stack-name securerag
aws cloudformation wait stack-delete-complete --region ap-south-1 --stack-name securerag
# 3. SSM parameters:
aws ssm delete-parameters --region ap-south-1 \
  --names /securerag/aes_key_b64 /securerag/api_keys_json /securerag/groq_api_key
# 4. Optional: IAM → Identity providers → remove token.actions.githubusercontent.com.
```
The next day, check Cost Explorer filtered by the tag `project=securerag` and confirm the charges
have stopped.

## Security notes specific to this deployment
See `SECURITY.md` → "AWS Lambda deployment". In short:
- secrets come only from SSM;
- the AES key never lands in an image layer;
- dev auth can't be deployed;
- the audit chain is per container (the full record is in CloudWatch Logs);
- rate limits are per container;
- the Function URL is public on purpose, and the app enforces API-key auth and RBAC.
