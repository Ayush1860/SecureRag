# SecureRAG — Pending AWS Work + Amplify Hosting (Lean Plan)

Repo: `https://github.com/Ayush1860/SecureRag`
Budget: **$20 of AWS credits, max.** This plan expects to spend **under $5**.
This replaces the earlier `AWS_PLAN.md`; KMS, S3 Object Lock and Bedrock are dropped from scope.

## What's in scope

1. **Pending item:** validate the Docker image and the Qdrant server backend. Both were written but never run, because Docker doesn't fit on the local disk.
2. **Hosting:** the React frontend goes on **AWS Amplify Hosting** and the FastAPI backend on **AWS Lambda** (container image + Function URL).

## Key decisions (and why)

- **The benchmark runs on GitHub Actions, not EC2.** The repo is public, so standard runners are free and have Docker, 4 vCPU, 16 GB RAM, ~14 GB disk and a 6-hour job limit. That's enough for the Docker build, compose with Qdrant, a 10k-doc run and probably 50k. Cost is $0. EC2 is only a fallback (§7).
- **Amplify can't run the backend.** Amplify hosts static sites and SSR frameworks, and a FastAPI + torch service doesn't fit there, so the backend goes to Lambda.
- **Lambda, not an always-on EC2 box.** A 24/7 `t4g.small` plus its public IPv4 costs about $15–17/month, which would eat the whole budget in about five weeks. Lambda's always-free allowance (400k GB-seconds/month) covers a demo easily. The Function URL also gives free HTTPS, which the browser needs: an HTTPS Amplify page can't call a plain-HTTP API.
- **Accepted trade-offs:** cold starts of about 15–30 s (torch import + model load), and a **read-only demo corpus** baked into the image. The frontend shows a "waking up backend" state while it polls `/api/ready`.
- **GitHub Actions builds the Docker image and pushes it to ECR,** so no local Docker is needed.
- **The hosted demo uses Chroma** (small corpus baked in) and Groq as the LLM (free API key, already implemented). Qdrant is exercised only in the benchmark.

## Cost estimate

| Item | Monthly |
|---|---|
| GitHub Actions (build, benchmark, deploy) | $0 |
| Amplify Hosting (static React build, low traffic) | ~$0 (1,000 build min, 5 GB stored, 15 GB served free) |
| Lambda compute (3 GB memory, demo traffic) | ~$0 (inside the always-free 400k GB-s) |
| ECR image storage (~2 GB; lifecycle policy keeps 2 images) | ~$0.20 |
| CloudWatch Logs (7-day retention) | ~$0 |
| SSM Parameter Store (standard SecureString, AWS-managed key) | $0 |
| **Total** | **< $1/month** |

**Never enable any of these:**
- Amplify WAF ($15/month per app)
- Lambda provisioned concurrency
- A NAT gateway
- An always-on EC2 instance

---

## 1. Manual steps before any prompt (console, ~20 min, $0)

1. **Budgets:** create a monthly cost budget of $20. Set alerts at $2, $5 and $10 (actual) and at $20 (forecast).
2. **Region:** put everything in `ap-south-1` (Mumbai). Latency is good for Indian viewers, and Lambda, ECR and Amplify are all available there. Tag every resource `project=securerag`.
3. **SSM Parameter Store:** create these as SecureString with the default `aws/ssm` key.
   - `/securerag/aes_key_b64`: a **new** demo key. Never reuse the local key.
   - `/securerag/groq_api_key`
   - `/securerag/api_keys_json`: the demo API keys, created in Prompt 3.
4. **GitHub → AWS OIDC:** in IAM → Identity providers, add `token.actions.githubusercontent.com`. The deploy role itself comes from the Prompt 3 template.

---

## 2. Session opener (paste once per Claude Code session)

```text
You are working on SecureRAG. Read AWS_PLAN.md and docs/SCALE_WORKLOG.md first.
The hard AWS budget is $20; this plan targets < $5.

Rules:
1. Plan mode first: files to change, new deps, tests, and any AWS resource with its
   estimated monthly cost. Wait for my approval.
2. Never create AWS resources yourself; write templates, workflows and scripts that I run.
3. Local behaviour, existing tests and CI stay unchanged unless a change is intentional
   and explained. Tests never touch real AWS (use moto or stubs).
4. Security invariants still hold: RBAC enforced server-side, AES-GCM with AAD,
   retrieved text is data, AUTH_MODE=dev never used in anything deployed.
5. IAM policies are least privilege with exact actions and ARNs.
6. Run pytest, ruff, mypy before and after. Append a section to docs/SCALE_WORKLOG.md.
   End with a conventional commit message. Do not push.
```

---

## 3. Prompt 1 — Docker + Qdrant benchmark on GitHub Actions ($0)

```text
Pending item: the Dockerfile, docker-compose.yml and the Qdrant server backend were
never run. Validate them on GitHub Actions.

1. benchmark_scale.py: add `--backend chroma|qdrant` and `--qdrant-url`. In qdrant mode,
   create a fresh collection per run and drop it afterwards. Record the backend and the
   runner's CPU/RAM in the report.
2. Add `--probe`: ingest 300 docs, measure chunks/sec, print projected wall time for
   1k/10k/50k, and refuse any run projected over `--max-hours` (default 4.5, leaving
   headroom under the 6-hour job limit).
3. .github/workflows/docker-bench.yml, workflow_dispatch only, inputs `docs`
   (default 10000) and `backend` (default qdrant):
   - Free disk first (remove preinstalled toolchains we don't need, e.g. android/dotnet),
     print `df -h`.
   - `docker compose build`, `docker compose up -d qdrant`, wait for health.
   - Smoke test: start the api container with AUTH_MODE=api_key and a throwaway AES
     key, create one key per role, hit /api/ready and /api/query for each role. Fail the
     job if any role sees a chunk outside its policy.
   - Probe, then run the benchmark inside the api image against Qdrant.
   - Upload reports/scale/gha_qdrant_docs_<n>.{md,json} as artifacts. No commits from
     CI; I'll download and commit them.
   - Print `df -h` after every heavy step; keep the job under 6 hours.
4. In the report, compare with the local Chroma runs, especially guest retrieval p95
   (156 ms at 50k with Chroma). That's the number Qdrant payload indexes should fix.
5. Update the "Not built locally" note in the worklog once the job passes.
```

Run 10k first. Run 50k only if the 10k probe projects it under 4.5 h. Download the reports and commit them locally.

---

## 4. Prompt 2 — Make the backend Lambda-ready

```text
Adapt the API to run on AWS Lambda as a container image without breaking the
Docker/compose or local paths.

1. Dockerfile: add a `lambda` target built on the existing runtime stage. Add the AWS
   Lambda Web Adapter extension (copied from its public ECR image at a pinned version)
   so uvicorn runs unchanged. AWS_LWA_READINESS_CHECK_PATH=/api/health, port 8000.
   Keep CPU-only torch.
2. Demo corpus: data/sample plus a deterministic 1k-doc synthetic corpus
   (scripts/generate_corpus.py with a fixed seed). Two options; compare cold-start time
   and security, recommend one, and wait for my choice:
   (a) ingest at build time in CI with the same AES key fetched from SSM and passed via
       `docker build --secret`, never written to an image layer;
   (b) ship the plaintext demo docs in the image and ingest into /tmp on cold start.
3. Lambda runtime mode (LAMBDA_MODE=1 or detected via AWS_LAMBDA_FUNCTION_NAME):
   - On cold start, copy/build the index under /tmp (the rest of the filesystem is
     read-only) and point CHROMA_DIR, STATE_DB_PATH, SPARSE_DIR there.
   - Load secrets from SSM Parameter Store (names from env) with boto3 at startup; fail
     fast if any are missing. No secrets in Lambda environment variables.
   - Audit events go to stdout as JSON (CloudWatch Logs). The hash chain is per
     container instance; SECURITY.md must say so plainly.
   - Disable admin ingest endpoints and anything that writes outside /tmp.
   - Rate limiting stays in-process (per container); document that limit.
4. CORS: the Function URL handles CORS. In LAMBDA_MODE, don't add FastAPI's CORS
   middleware; duplicate Access-Control-Allow-Origin headers break browsers.
5. Frontend: read the API base from VITE_API_BASE (default "" so local and compose keep
   using relative /api). While /api/ready fails, show "Waking up the demo backend
   (~20 s)" with retry instead of an error.
6. Tests: LAMBDA_MODE with moto-stubbed SSM (secrets load, missing-secret failure),
   /tmp relocation, admin endpoints disabled, no CORS middleware in Lambda mode.
```

---

## 5. Prompt 3 — Infrastructure + deploy pipeline

```text
Write the AWS infrastructure and the deploy workflow. Region ap-south-1.

1. deploy/aws/backend.yaml (CloudFormation), everything tagged project=securerag:
   - ECR repository, scan-on-push, lifecycle policy keeping the last 2 images.
   - Lambda function: PackageType Image, x86_64, MemorySize 3008, Timeout 60,
     EphemeralStorage 1024 MB, ReservedConcurrentExecutions 2 (caps cost and abuse).
   - Execution role: logs for its own log group; ssm:GetParameter(s) on /securerag/*
     only; kms:Decrypt on the aws/ssm key with a kms:ViaService condition.
   - Log group with 7-day retention.
   - Function URL, AuthType NONE (app-level API keys do auth), CORS AllowOrigins =
     parameter AmplifyOrigin, methods GET/POST, headers Authorization, Content-Type,
     X-API-Key.
   - GitHub OIDC deploy role trusted only for repo Ayush1860/SecureRag on
     refs/heads/main; may push to this ECR repo and call lambda:UpdateFunctionCode
     on this function only.
   - Outputs: Function URL, ECR URI, deploy role ARN.
   Validate with cfn-lint and add it to CI.
2. .github/workflows/deploy-backend.yml (workflow_dispatch, plus push to main touching
   securerag/, app/ or Dockerfile): OIDC login, build the `lambda` target, push to ECR
   tagged with the git SHA, update the function, then smoke-test /api/ready and one
   query per demo role on the Function URL. Roll back to the previous image tag if the
   smoke test fails.
3. scripts/create_demo_keys.py: generate guest/employee/exec keys, print them once, and
   write the hashed-keys JSON I'll paste into /securerag/api_keys_json.
4. deploy/aws/README.md: first deploy (stack → SSM params → first image push → update
   stack with image URI), redeploy, rotate demo keys, teardown (delete stack, ECR images,
   SSM params, Amplify app). Give a cost for each step.
```

---

## 6. Prompt 4 — Amplify frontend

```text
Prepare the React frontend for AWS Amplify Hosting.

1. Add amplify.yml at the repo root with appRoot `frontend`: npm ci, npm run build,
   artifacts from dist, cache node_modules.
2. Document the SPA rewrite (200 rewrite of non-file paths to /index.html) in
   deploy/aws/README.md so I can paste it into the Amplify console.
3. Security headers via customHeaders in amplify.yml: a Content-Security-Policy whose
   connect-src allows only 'self' and the Function URL (placeholder I'll replace),
   X-Content-Type-Options, Referrer-Policy, frame-ancestors 'none'.
4. An "About this demo" panel: synthetic data only, three demo roles, cold-start note,
   links to the repo and the benchmark reports.
5. Demo credentials: the page may show the three demo API keys, since the data is
   synthetic and the point is to show RBAC. Reserved concurrency 2, per-key rate limits
   and Groq's free-tier limits bound the cost. Record this reasoning in SECURITY.md.
```

Manual Amplify steps:
1. In the Amplify console, choose Host web app → GitHub, then select the repo and `main`. Amplify picks up `amplify.yml`.
2. Set the env var `VITE_API_BASE=<Function URL>` and deploy.
3. Put the Amplify domain into the backend stack's `AmplifyOrigin` parameter and update the stack.

**Leave WAF off.**

---

## 7. Fallback only: EC2 for the 50k Qdrant run

Use this only if the GitHub Actions 50k run can't fit in the 6-hour limit.
- **Instance:** `m7i-flex.large` (Free-plan eligible, 8 GB RAM) with 30 GB gp3.
- **Access:** no inbound rules; use SSM.
- **Cost:** about 4–6 hours ≈ **$1–2**. Terminate the instance and delete the volume the same day.

---

## 8. Order of work

| Step | Where | Cost |
|---|---|---|
| Manual steps (§1) | Console | $0 |
| Prompt 1: Docker + Qdrant benchmark | GitHub Actions | $0 |
| Prompt 2: Lambda-ready backend | Local | $0 |
| Prompt 3: CFN + deploy workflow, first deploy | Local → AWS | ~$0.20/mo |
| Prompt 4: Amplify frontend | Local → Amplify | ~$0 |
| §7 fallback, only if needed | EC2 | ~$1–2 once |

## 9. Teardown

1. Delete the Amplify app.
2. Delete the CloudFormation stack.
3. Delete the ECR images, if the stack kept the repository.
4. Delete the `/securerag/*` SSM parameters.
5. The next day, check Cost Explorer (filter `project=securerag`) to confirm charges have stopped.
