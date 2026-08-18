# document-scanner

Checkbox detection API. Given a document image, detects checkboxes and
classifies each as checked or unchecked. See `challenge.pdf` (pages 1-2) for
the full spec.

## API

```
POST /detect
```

- **Input:** a document image, sent as a file upload.
- **Output:**

  ```json
  {
    "boxes": [
      { "bbox": [x1, y1, x2, y2], "is_checked": true },
      { "bbox": [x1, y1, x2, y2], "is_checked": false }
    ]
  }
  ```

  `bbox` is the pixel coordinates of the top-left and bottom-right corners
  of each detected checkbox.

## Architecture

```
API Gateway (HTTP API) --POST /detect--> Lambda "detect" (Python, container image)
                                            --> classic CV pipeline (OpenCV: contours +
                                                geometric filtering + ink-density check)
                                            --> Textract FORMS analysis (label-position
                                                hints only, for gap recovery)
```

A classic computer-vision pipeline running in a Python Lambda
(`lambda/detect/`, deployed as a container image): grayscale + adaptive
threshold -> `findContours` -> geometric filtering (size, aspect ratio,
corner count, rectangularity) to find checkbox-shaped candidates ->
IoU-based deduplication of nested contours -> per-box classification via
ink-density + a containment check (a mark is "checked" only if it's sized
to fit inside the box, not a fragment of a longer stroke passing through
it). All tunable constants live at the top of `detect_cv.py`.

This pipeline also calls AWS Textract's `AnalyzeDocument` (`FORMS` feature
type) as an enhancement layered on top, purely for label→checkbox location
hints (`textract_hints.py`) — never for Textract's own checked/unchecked
call, which has a known false-positive failure mode (a stray mark crossing
a box's border reads as "selected"). When a label's associated checkbox
wasn't found by the pixel pipeline at all, this hint drives a small local
search to recover it; classification still goes entirely through this
project's own logic. See `docs/algorithm-known-issues.md` issue #7 for the
full mechanism, and issues #9/#10 for two real misses (including the
project's original motivating case) this recovered. This call is a pure
enhancement, not a dependency: any Textract failure (network, credentials,
throttling) falls back to CV-only results rather than breaking the
request.

`handler.py`'s error responses never leak internal detail to the caller: a
bad request body maps to a generic `400`, and any other unexpected
exception maps to a generic `500` with a fixed `{"error": "internal
error"}` body — the full exception is logged via `logging.exception`
first, which Lambda automatically ships to CloudWatch Logs, so the detail
is still there for debugging without ever reaching the response.

- `infra/` — Terraform: API Gateway HTTP API, the Lambda function
  (container-image), its IAM role (CloudWatch Logs + `textract:AnalyzeDocument`),
  its ECR repository, CloudWatch log group.
- `lambda/detect/` — Python Lambda source: `detect_cv.py` (core detection
  algorithm), `textract_hints.py` (Textract FORMS integration for gap
  recovery), `handler.py` (Lambda entry point), `cli.py` (local dev/tuning
  tool, not deployed), `Dockerfile` (container image — required because
  `opencv-python-headless` needs native shared libraries, so this Lambda
  is `package_type = "Image"` rather than zip-deployed), `tests/` (pytest
  suite).
- `samples/` — 4 real appraisal-document images (extracted from
  `challenge.pdf`'s embedded sample images) for testing detection quality.
  `samples/results/<timestamp>/` is where local runs write raw JSON
  responses and annotated (bbox-overlaid) PNGs — `<timestamp>` identifies
  the run, so different runs never collide and can be diffed against each
  other. This tree is gitignored — it's a local, per-run scratch area, not
  committed — see "Testing" below.
- `scripts/annotate_detections.py` — draws detected boxes over the sample
  images (green = checked, red = unchecked) for visualizing a
  `call_detect_api.sh` results folder. (`cli.py`'s local dev loop already
  writes its own annotated output directly — see "Run it locally" below —
  this script is for visualizing responses from the deployed endpoint
  instead.)

## Try it now

Two ways to see this work before touching AWS, Docker, or Terraform at all:

### Run it locally

`cli.py` runs the same detection code (`detect_cv.py`) directly against
image files on disk — no Lambda, no API Gateway, no AWS account, no
deploy. It's the fastest loop for seeing the algorithm work on real
documents:

```bash
cd lambda/detect
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt   # one-time, see Prerequisites
venv/bin/python3 cli.py ../../samples
```

That runs it against the 4 bundled sample appraisal forms and writes, per
image, into a fresh `samples/results/<timestamp>/` folder:

- `<name>.json` — the raw `{"boxes": [...]}` response
- `<name>-annotated.png` — the detected boxes drawn directly on the image
  (green outline = `is_checked: true`, red = `false`), so you can see
  whether it worked at a glance instead of reading raw coordinates

(`<timestamp>` is a UTC value like `20260816T143022Z`, generated fresh
each run, so successive runs never overwrite each other and can be
diffed or eyeballed side by side. This tree is gitignored — it's local
scratch output, not committed.) Point `cli.py` at any single image or
folder of your own in place of `../../samples` to try it on other
documents. This is also the loop used to calibrate the constants at the
top of `detect_cv.py` (`MIN_BOX_SIZE`, `MAX_BOX_SIZE`, `MIN_EXTENT_RATIO`,
etc.) — run it, eyeball the annotated PNGs, adjust a constant, repeat.

No AWS credentials are required for this to run. The one exception:
`detect_checkboxes` also calls Textract's FORMS analysis for
gap-recovery hints (see "Architecture" above, and
`docs/algorithm-known-issues.md` issue #7) — this is the one part of the
pipeline that isn't purely local, but it fails open: missing/expired
credentials just mean the run falls back to CV-only results (logged, not
raised), so `cli.py` still works either way, just without the
Textract-recovered boxes.

The pytest suite (`tests/`) exercises the same code and never needs AWS
credentials at all — it stubs the Textract call out:
`venv/bin/python3 -m pytest` from `lambda/detect/`.

### Or call the already-deployed endpoint

No local setup at all — this hits a live, currently-deployed copy of this
API directly:

```bash
curl -X POST https://quslj98to1.execute-api.us-east-1.amazonaws.com/detect \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

This URL is whatever `terraform apply` last produced in the dev
environment — a convenience snapshot for trying the API out, not a stable
contract; it changes if the API Gateway resource is ever destroyed and
recreated.

Once you've deployed your own copy, "Testing" further down has more ways
to exercise it: a folder of images at once, the 4 bundled samples with
known-good counts to check against, and visualizing results.

## Prerequisites

This project pins tool versions with [asdf](https://asdf-vm.com/). Install
asdf itself first, then:

```bash
asdf plugin add terraform
asdf plugin add awscli
asdf plugin add python
asdf plugin add github-cli
asdf install
```

This installs the exact Terraform, AWS CLI, Python, and GitHub CLI
versions listed in `.tool-versions`. GitHub CLI (`gh`) isn't required to
develop or deploy this project — it's only used for inspecting GitHub
Actions runs (see "CI/CD" below) and is optional.

### Docker

Building and pushing the Lambda's container image (`lambda/detect/`)
requires [Docker](https://docs.docker.com/get-docker/) (e.g. Docker
Desktop) to be installed and running locally — Terraform shells out to
`docker build`/`docker push` as part of `terraform apply` (see "Deploy"
below). Docker is **not** asdf-managed: asdf pins language/CLI tool
versions, but a container runtime is a different kind of dependency, so
install it the normal way for your OS.

You don't need Docker just to run the algorithm locally, though — the
`cli.py` dev loop (see "Run it locally" under "Try it now" above) only
needs a plain Python virtualenv.

### Lambda (Python) dependencies

`lambda/detect/requirements.txt` (runtime: `opencv-python-headless`,
`numpy`, `boto3`) and `requirements-dev.txt` (adds `pytest`) are
committed. For local development (running `cli.py` or the test suite),
set up a virtualenv once:

```bash
cd lambda/detect
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
```

This venv is only needed for local dev/testing — it's not used at deploy
time. The deployed Lambda instead builds `requirements.txt` into a
container image via `Dockerfile` (see "Docker" above and "Deploy" below).

Run the tests with `venv/bin/python3 -m pytest` from `lambda/detect/`.
Running `cli.py` (but not the test suite) also needs AWS credentials with
`textract:AnalyzeDocument` — see "Run it locally" above.

### AWS account setup (manual, one-time)

Terraform provisions the app's own resources (Lambda, its execution role,
API Gateway), but it can't create the credentials it runs with, and it
can't create your IAM user for you either — that's on you, once, per AWS
account. **Never do any of this as the AWS root user** — root has
unrestricted account access with no way to scope it down.

**1. Create a dedicated IAM user, as an admin/root (one-time bootstrap only):**

```bash
aws iam create-user --user-name document-scanner-deployer

# Console password, forced reset on first login
aws iam create-login-profile \
  --user-name document-scanner-deployer \
  --password '<some-temporary-password>' \
  --password-reset-required

# Required so this user is even allowed to run `aws login`
aws iam attach-user-policy \
  --user-name document-scanner-deployer \
  --policy-arn arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess

# The actual deploy permissions (Lambda, API Gateway, IAM role for the
# Lambda's own execution role, CloudWatch Logs, Textract, ECR for the
# Lambda's container image) - a customer-managed policy, not inline:
# AWS caps inline IAM user policies at 2048 bytes, too small for this
# policy's fully-enumerated action lists once ECR grew past a handful of
# actions (confirmed live, not theoretical - a real LimitExceeded error).
# Managed policies cap at 6144 bytes, comfortable headroom.
aws iam create-policy \
  --policy-name document-scanner-deploy \
  --policy-document file://infra/bootstrap-iam-policy.json

aws iam attach-user-policy \
  --user-name document-scanner-deployer \
  --policy-arn arn:aws:iam::<your-account-id>:policy/document-scanner-deploy
```

This is the *only* step here that needs root/admin credentials. Everything
below runs as `document-scanner-deployer`.

**Updating this policy later** (e.g. adding a new permission) also needs
root/admin, since the deployer can't grant itself new permissions by
design:

```bash
# Managed policies keep up to 5 versions - delete the oldest first if
# already at that cap (list-policy-versions to check).
aws iam create-policy-version \
  --policy-arn arn:aws:iam::<your-account-id>:policy/document-scanner-deploy \
  --policy-document file://infra/bootstrap-iam-policy.json \
  --set-as-default
```

The GitHub Actions deploy role's copy of this same policy (see "CI/CD"
below) is attached as an inline role policy, not managed — inline *role*
policies cap at 10,240 bytes, so there's no size pressure there, and it's
Terraform-managed (`infra/github_oidc.tf` reads this same JSON file
directly), updating automatically on every `terraform apply`. Only the
human deployer's copy needs this manual step.

**2. Sign in to the AWS Console as that user** (not root) at
`https://<your-account-id>.signin.aws.amazon.com/console`, using the
username/password from step 1. You'll be forced to set a new password.

**3. Log in the CLI as that user, into a named profile** (do *not* use the
`default` profile if it's already tied to root):

```bash
aws login --profile document-scanner
```

This opens your browser to pick a console session; select the
`document-scanner-deployer` session. (`aws login --profile X --remote` for
SSH/headless.)

> **Gotcha:** if `aws sts get-caller-identity --profile document-scanner`
> ever fails with "profile could not be found" right after this, check
> `~/.aws/config` — non-default profiles must be written as
> `[profile document-scanner]`, not `[document-scanner]`.

**4. Bridge the login-session credentials for Terraform.** As of AWS CLI
2.36, `aws login` credentials use a newer credential type
(`login_session`) that Terraform's AWS provider doesn't understand yet.
Add a second profile that shells back out to the CLI to export plain
temporary credentials:

```ini
# ~/.aws/config
[profile document-scanner-tf]
credential_process = aws configure export-credentials --profile document-scanner --format process
region = us-east-1
```

Use `document-scanner-tf` (not `document-scanner`) for anything that isn't
the `aws` CLI itself — i.e. for Terraform.

Set a default region that supports both Lambda and Textract (e.g.
`us-east-1`) — Textract isn't available in every region.

Verify:

```bash
aws sts get-caller-identity --profile document-scanner     # -> document-scanner-deployer, not root
aws sts get-caller-identity --profile document-scanner-tf  # -> same, via credential_process
```

**Session lifetime:** `aws login` credentials expire after up to 12 hours
(auto-refreshed while active); re-run `aws login --profile
document-scanner` once it's been idle a while and Terraform starts
failing auth.

**Non-interactive alternative (CI, no browser):** skip `aws login`
entirely and use a static access key instead —
`aws iam create-access-key --user-name document-scanner-deployer`, then
put `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` wherever your CI stores
secrets. The IAM user and policies from step 1 are the same either way.

> **TODO:** this per-person IAM user + managed policy is a shortcut
> acceptable for a single-developer/personal account. It does not scale
> to a real team: every new developer would mean provisioning another
> long-lived IAM user by hand. For a production account, replace this
> with **IAM Identity Center (SSO) + a permission set** granting the
> `infra/bootstrap-iam-policy.json` permissions — new developers get
> assigned to the permission set centrally, authenticate with their own
> SSO identity via `aws sso login`, and there are no long-lived
> credentials to create or rotate per person. CI/CD should similarly move
> to an OIDC-federated role rather than a static access key.

> **Lesson from a real incident, not a hypothetical:** while fixing the
> ECR-repository-policy gotcha below, the first attempt tried adding the
> new `ecr:SetRepositoryPolicy` permission via `aws iam put-user-policy`
> (an *inline* user policy) rather than checking that a managed policy
> already existed. Inline user policies cap at 2048 bytes, too small for
> this policy's fully-enumerated action lists — so the first fix attempt
> wildcarded several statements (`"ecr:*"`, `"lambda:*"`, `"iam:*"`,
> `"s3:*"`) to fit under that cap, a real, live-applied security
> regression, not a proposal — an automated review of the commit caught it
> before it reached `main`. Corrected by finding the pre-existing
> customer-managed `document-scanner-deploy` policy (6144-byte limit, no
> wildcards needed) that was already attached to the deployer, updating
> *that* instead, and deleting the redundant inline policy the first
> attempt had created alongside it. The bootstrap instructions above
> reflect the corrected (managed-policy) setup. Worth remembering: a
> byte-limit error is a signal to find the right mechanism, not a license
> to broaden a grant to make it fit.

> **Gotcha (confirmed live, cost ~30 minutes to diagnose):** after
> renaming/recreating the Lambda's ECR repository (e.g. the `-cv` suffix
> removal that happened alongside this repo's Go-Lambda removal — see git
> history), `terraform apply` may fail creating the Lambda function with
> `AccessDeniedException: Lambda does not have permission to access the
> ECR image. Check the ECR permissions.` This is **not** simple
> propagation delay (ruled out with 11+ retries over ~20 minutes, spaced
> up to 45s apart) and **not** a stale image (ruled out by rebuilding and
> re-pushing a completely fresh image — same failure) and **not** about
> IAM role identity (ruled out by testing with a brand-new, never-before-used
> role — same failure). The actual fix: a **brand-new ECR repository needs
> an explicit repository policy granting the Lambda service principal pull
> access** (`aws_ecr_repository_policy` in `infra/detect.tf`, statement
> `LambdaECRImageRetrievalPolicy`) — same-account "implicit" access isn't
> reliable enough to depend on. If this error recurs after some other
> resource rename in the future, check that this repository policy exists
> and is attached to whatever the current repository is before assuming
> it's propagation delay again.

## Deploy

```bash
cd infra
export AWS_PROFILE=document-scanner-tf   # skip if using a static access key / CI
terraform init
terraform plan
terraform apply
```

**State is remote, shared, and versioned** — an S3 bucket
(`infra/terraform_state.tf`, versioning + encryption + public access
blocked), using Terraform's native S3 locking (`use_lockfile`, no DynamoDB
table needed). This is required, not just nice-to-have: GitHub Actions CI
(see "CI/CD" below) runs on ephemeral runners with zero local state on
every run — without shared state, CI would try to re-create every resource
that already exists and fail with `EntityAlreadyExists`/
`ResourceAlreadyExistsException` (confirmed live, the first CI deploy
attempt failed exactly this way before the backend was added). Both the
human deployer and CI read/write the same state via the `backend "s3"`
block in `infra/versions.tf` — there's nothing to configure locally beyond
the normal `terraform init`.

**If you're bootstrapping this on a brand-new AWS account** (e.g. a fork,
per the CI/CD section's fork instructions below), this bucket won't exist
yet — chicken-and-egg, since the backend config needs the bucket, but the
bucket is itself Terraform-managed. Sequence: temporarily comment out the
`backend "s3"` block in `infra/versions.tf`, run `terraform apply` with
local state (creates the bucket and everything else), then uncomment the
backend block, update the bucket name in it to match your account ID, and
run `terraform init -migrate-state` to move local state into it.

Terraform will build + push the Lambda's container image (`docker build
--platform linux/arm64 ...` against `lambda/detect/Dockerfile`, then
`docker push` to a per-project ECR repo it creates) as part of the apply —
Docker must be running locally for this step (see "Docker" under
Prerequisites). On success it prints `detect_endpoint`, the full URL of
the deployed route.

> **Gotcha:** right after creating/updating the IAM user's managed policy
> (step 1 above), the very first `terraform apply` may fail with
> `AccessDeniedException` on read-only calls (e.g.
> `lambda:GetFunctionCodeSigningConfig`, `logs:DescribeLogGroups`) even
> though the policy is correct — IAM permission changes can take a minute
> or two to propagate. Just re-run `terraform apply`.

> **Gotcha:** if the Docker build/push step fails with something like
> `InvalidParameterValueException: image manifest ... not supported`,
> it's because some Docker Desktop versions' `buildx` builder emits
> provenance/SBOM attestation manifests by default that Lambda's container
> image support rejects. `infra/detect.tf` already passes
> `--provenance=false --sbom=false` to `docker build` to avoid this, but if
> you hit an equivalent error with a different Docker setup/version, that
> flag pair is the fix to look for.

## CI/CD

`.github/workflows/deploy.yml` runs on every push and pull request against
`main`:

- **`test` job** (always runs): runs the full `lambda/detect` pytest suite
  and checks Terraform formatting.
- **`deploy` job** (push to `main` only, after `test` passes): runs
  `terraform apply` for real, then smoke-tests the deployed `/detect`
  endpoint against `samples/appraisal-1.png` and fails the job if it
  doesn't return `HTTP 200`.

**Authentication:** no AWS access keys are stored in GitHub. The `deploy`
job assumes an IAM role (`document-scanner-dev-github-actions-deploy`,
defined in `infra/github_oidc.tf`) directly via GitHub's OIDC token
federation. That role's trust policy is scoped to this exact repo and the
`main` branch only — a workflow run from a fork or any other branch cannot
assume it, and there's no long-lived secret that could leak. The role has
the same deploy permissions as the human bootstrap user
(`infra/bootstrap-iam-policy.json`, attached to both), so CI can run the
exact `terraform apply` a human deployer would.

> **Gotcha:** the trust condition matches on the token's `sub` claim, and
> GitHub's actual `sub` format is **not** the plain `repo:owner/repo:ref:...`
> commonly shown in examples — it's
> `repo:owner@<owner_id>/repo@<repo_id>:ref:refs/heads/main`, using GitHub's
> permanent numeric IDs for the account and repo rather than their
> (renameable/transferable) names. A plain-name trust condition was tried
> first here and consistently failed with `Not authorized to perform
> sts:AssumeRoleWithWebIdentity` — diagnosed by adding a temporary workflow
> step that decoded and printed the actual token's claims. `github_owner_id`
> / `github_repo_id` in `infra/github_oidc.tf` hold the real values for this
> repo; if this ever needs debugging again, decode a live token rather than
> trusting the commonly-documented plain-name format.

**One-time setup, already done for this repo** (documented here in case
this is ever forked/re-bootstrapped): the OIDC provider and role are
Terraform-managed (`infra/github_oidc.tf`), applied the same way as
everything else in `infra/` — no separate manual AWS Console step beyond
what "AWS account setup" above already covers. If you fork this repo and
want your own fork to deploy: update `github_repo`, `github_owner_id`, and
`github_repo_id` in `infra/github_oidc.tf` to your fork's values (find your
repo's numeric ID via `gh api repos/OWNER/REPO --jq .id` and your account's
via `gh api users/OWNER --jq .id`) before applying, and update the
hardcoded `role-to-assume` ARN in `.github/workflows/deploy.yml` to match
your own AWS account ID once the role exists
(`terraform output github_actions_deploy_role_arn`).

## Testing

See "Try it now" near the top for the fastest ways to run this with no
setup at all — `cli.py` against local files, or `curl` against the live
dev endpoint. Once you've deployed your own copy (see "Deploy" above),
here's more ways to exercise it:

### Quick check with any image

```bash
curl -X POST "$(terraform -chdir=infra output -raw detect_endpoint)" \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

### Test against a folder of images

`scripts/call_detect_api.sh` sends every `.png`/`.jpg`/`.jpeg` in a folder
to the endpoint you give it, prints a one-line summary per file (HTTP
status, boxes detected, checked/unchecked counts), and saves each raw
JSON response into a timestamped `results/<timestamp>/` subfolder it
creates alongside the images:

```bash
./scripts/call_detect_api.sh <folder> [endpoint_url]
```

`endpoint_url` is optional — it defaults to
`terraform -chdir=infra output -raw detect_endpoint`.

```bash
./scripts/call_detect_api.sh samples
```

Works against any folder, not just `samples/`.

Each run's output lives at `results/<timestamp>/<name>.json`, so running
the script twice against the same folder never overwrites a previous
run's files — every run gets its own timestamp.

### Test against the sample appraisal documents

`samples/appraisal-1.png` through `appraisal-4.png` are the 4 real sample
images from the challenge (extracted from `challenge.pdf`'s embedded
images — actual Fannie Mae/Freddie Mac appraisal forms full of checkboxes,
both filled and empty):

```bash
./scripts/call_detect_api.sh samples
```

Or by hand, one file at a time:

```bash
ENDPOINT=$(terraform -chdir=infra output -raw detect_endpoint)

for i in 1 2 3 4; do
  echo "=== appraisal-$i.png ==="
  curl -s -X POST "$ENDPOINT" \
    -F "file=@samples/appraisal-$i.png" \
    -o "/tmp/resp-$i.json" -w "HTTP %{http_code}\n"
done
```

Summarize a response (total boxes detected, checked vs. unchecked):

```bash
python3 -c "
import json
d = json.load(open('/tmp/resp-1.json'))
boxes = d['boxes']
checked = sum(1 for b in boxes if b['is_checked'])
print(f'Total boxes: {len(boxes)}  Checked: {checked}  Unchecked: {len(boxes) - checked}')
"
```

Or pretty-print one box to check the shape:

```bash
python3 -m json.tool /tmp/resp-1.json | head -10
```

Known-good results as of the last verified deploy (these are dense,
multi-page appraisal forms; small shifts are normal if `detect_cv.py`'s
constants are ever recalibrated):

| Sample            | Boxes detected | Checked | Unchecked |
| ------------------ | -------------- | ------- | --------- |
| appraisal-1.png    | 118            | 37      | 81        |
| appraisal-2.png    | 43             | 16      | 27        |
| appraisal-3.png    | 48             | 12      | 36        |
| appraisal-4.png    | 79             | 28      | 51        |

Expected response shape:

```json
{
  "boxes": [
    { "bbox": [489, 1455, 544, 1502], "is_checked": true },
    { "bbox": [489, 1405, 544, 1452], "is_checked": false }
  ]
}
```

Raw responses aren't left in `/tmp` — running `scripts/call_detect_api.sh`
(or `cli.py`) against `samples/` saves each run's output under
`samples/results/<timestamp>/appraisal-*.json`, so it can be diffed
against or inspected later without re-running anything. This tree is
gitignored (results are regenerated locally, not committed), so "the last
verified run" is whatever you last ran locally — re-run the commands
above to reproduce the numbers in the table.

### Visualizing detections

The JSON alone is hard to sanity-check. `cli.py` (see "Run it locally"
above) already writes
`samples/results/<timestamp>/appraisal-{1..4}-annotated.png` as part of
every run — no separate step needed. For visualizing a
`call_detect_api.sh` results folder instead (e.g. from testing the live
deployed endpoint), `scripts/annotate_detections.py` draws every detected
box over its source image the same way (green outline =
`is_checked: true`, red = `false`). It takes the results folder to read
JSON from (and write annotated PNGs into) as its second argument:

```bash
python3 -m venv /tmp/annotate-venv && /tmp/annotate-venv/bin/pip install --quiet Pillow
RUN_DIR=samples/results/<timestamp>  # from the call_detect_api.sh output above
/tmp/annotate-venv/bin/python3 scripts/annotate_detections.py samples "$RUN_DIR"
```

Outputs `$RUN_DIR/appraisal-{1..4}-annotated.png` alongside the JSON responses
already in that folder.

On the last verified visual review (eyeball every box against the source image):
both of the two concrete failure cases that motivated building the CV
pipeline are now fixed. The "appraisal-2.png scratch line" case (see
"Next steps" for the detail) took a second mechanism to actually close —
pixel/geometry fixes alone changed its failure mode but never recovered
it; it's fixed now via Textract-hint gap recovery (issue #7), a
label-awareness pass layered on top of the pixel pipeline, not a further
pixel tweak. On `appraisal-4.png`, the "Utilities" checkbox grid
(Electricity/Gas/Water/Sanitary Sewer) that Textract dropped entirely
under a diagonal watermark is fully detected, with each box correctly
classified. Overall box placement is tight and classification matches the
visible marks on both reviewed pages.

A previously-documented gap on `appraisal-1.png` (118 detected against an
estimate of ~125 expected, from an unverified prior manual analysis) was
investigated further and closed 2026-08-17: a full manual visual audit of
the entire rendered page found every checkbox correctly boxed and no
spurious boxes, and the suspected source of the "~125" figure turned out
to have no verifiable, pixel-grounded basis. 118 is very likely the
correct count; see `docs/algorithm-known-issues.md` issue #5 for the full
investigation. Ongoing algorithm work — root causes, evidence, and open
items — is tracked in `docs/algorithm-known-issues.md`, not here.

## Viewing logs

```bash
aws logs tail /aws/lambda/document-scanner-dev-detect --profile document-scanner --follow
```

(Swap in `terraform -chdir=infra output -raw lambda_function_name` if the
environment/function name ever changes from the default `dev`.)

## Tear down

```bash
cd infra
terraform destroy
```

(This also deletes the Lambda's ECR repository and the container image in
it — `force_delete = true` on `aws_ecr_repository.detect` so
`terraform destroy` doesn't get blocked by leftover images.)

## Next steps

The CV detection pipeline is deployed behind the API Gateway and
validated against all 4 sample images, including a visual accuracy review
of each (see "Visualizing detections" above). It fixes both of the two
concrete Textract failure cases that originally motivated building it —
including the appraisal-2 scratch-line case, which took a second,
different mechanism (Textract-hint gap recovery, not another
pixel/geometry fix) to actually close; see the first item below for what
that took.

**The original Go/Textract-only implementation (`/detect-textract`) was
removed 2026-08-17**, once the CV pipeline's own Textract-hint gap
recovery (issue #7, below) meant there was no longer anything left to
compare it against — this project's own history of using Textract as the
detection method, including the two concrete failure cases that motivated
building the CV pipeline in the first place, is preserved in
`docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md` and
`docs/algorithm-known-issues.md`'s "Reference-data caveat" even though the
endpoint itself no longer exists.

Remaining ideas, roughly in priority order:

- **Context/label-aware gap recovery (issue #7): built, level 2 only.**
  The CV pipeline now calls Textract's FORMS analysis (`textract_hints.py`)
  purely for label→checkbox location hints — never for its own
  checked/unchecked call, which has the exact false-positive failure mode
  (a stray mark crossing a border reads as "selected") that motivated this
  whole CV pivot. `find_missing_boxes()` diffs those hints against the CV
  pipeline's own candidates and does a small, scoped local search at any
  gap — this is what finally closed the appraisal-2 scratch-line case
  (below), plus two other previously-open misses on the same page (a
  faint border, and a checkbox obscured by an unusual mark pattern — see
  `docs/algorithm-known-issues.md` issues #1/#9/#10). Not built: an LLM
  fallback for a label Textract's own form model never resolves a
  checkbox for at all (hit in live data — "Other (describe)" on
  appraisal-2), and level 1 (flagging a detected box with no nearby label
  as a likely false positive) — checked live and explicitly *not*
  pursued, since Textract's own checkbox recognition misses 20 of the CV
  pipeline's 118 correctly-detected boxes on appraisal-1 alone; using that
  as a removal signal would delete real boxes.
- **appraisal-2.png scratch line: now fixed, via label awareness rather
  than a pixel/geometry technique.** The original Textract failure was a
  false positive: the stray diagonal scratch line crossing the "No
  Zoning" checkbox (around x=718, y=503 in the 1586x846 image) made
  Textract read it as `is_checked: true`. Getting the CV pipeline to
  correctly recognize the mark as unrelated and classify red/unchecked
  turned out to need two separate fixes: first, the box had to be
  *detected* at all — verified directly, it never appeared in
  `detect_checkboxes`'s output prior to issue #7, regardless of four
  structurally different pixel/geometry attempts (morphological closing,
  a Hough-line rectangle reconstruction tolerant of partial occlusion,
  diagonal-stroke removal in 3 filter variants, and a fragment-clustering
  local-search scope — all tried and rejected on concrete evidence, see
  `docs/algorithm-known-issues.md` issue #9 for the full detail). What
  actually closed it: Textract's own FORMS analysis already resolves a
  checkbox for the "No Zoning" label, precisely, even though its own
  checked/unchecked call for that same box is wrong for the reason above
  — issue #7 uses only the location, then classifies with the CV
  pipeline's own (already correct) logic. Live-validated: recovered with
  the correct `is_checked: false`, despite the scratch line running
  straight through it.
- **Document-size / DPI generalization:** the pipeline's tunable
  constants (`MIN_BOX_SIZE`, `MAX_BOX_SIZE`, `MIN_EXTENT_RATIO`, etc., all
  at the top of `lambda/detect/detect_cv.py`) are calibrated in pixels
  against this specific document family's rendering — the 4 sample
  appraisal forms, whose pixel dimensions themselves already range from
  2550x4200 (`appraisal-1.png`, `appraisal-3.png`) down to 1586x846
  (`appraisal-2.png`). Checkbox pixel size will vary further across other
  document types, scans, or DPIs — these constants aren't guaranteed to
  generalize and would need recalibration, or ideally a DPI/scale-adaptive
  approach (e.g. deriving
  size thresholds from the source image's resolution rather than
  hardcoded pixel counts), before trusting the CV pipeline on documents
  very different from the 4 samples.
- **appraisal-1.png box count:** closed, not a gap — see "Visualizing
  detections" above and `docs/algorithm-known-issues.md` issue #5. A full
  visual audit found 118 to be correct; the "~125" estimate had no
  verifiable source.
- **Containment check sufficiency:** the design's reserved fallback
  (Canny + `HoughLinesP` diagonal-stroke detection as an additional
  classification signal) was deliberately not built, since the simpler
  containment check already resolved the appraisal-4 false-negative case
  on the 4 samples, and (after a later fix) correctly handles a checkbox
  border touching an adjacent table gridline — both when the mark inside
  stays clear of the border and when a hand-drawn mark overshoots into a
  border that's also touching a gridline (see `tests/test_detect_cv.py`:
  `test_checked_when_border_fused_with_gridline_and_mark_is_separate` and
  `test_checked_when_mark_touches_border_that_is_also_fused_with_gridline`).
  The appraisal-2 scratch-line case turned out not to be a containment-check
  problem at all — see "appraisal-2.png scratch line" above; the mark
  simply never reached classification in the first place (the box itself
  wasn't detected), and once issue #7 recovers the box, this same
  containment check classifies it correctly with no changes needed.
  Revisit the Hough-line fallback if a document with a harder version of
  the "unrelated mark near a checkbox" problem is encountered. **Specific, currently-untested
  gap** (see `docs/algorithm-known-issues.md` issue #4): `is_checked()`
  requires ink coverage above a threshold and that enough of the touching
  ink's connected component falls within the box's own bounds
  (`CONTAINMENT_INTERIOR_RATIO`), but has no shape/structure requirement —
  an isolated dust speck, hole-punch shadow, or print artifact that's
  fully contained inside a checkbox and covers enough of the interior
  would currently be classified `checked`, which Hough-line diagonal
  detection would have correctly rejected (no line structure) but the
  containment check does not. No test exercises this case in either
  direction. Add a targeted test case before trusting this pipeline on
  documents beyond the 4 current samples.
- **Size limits:** the pipeline's own upload guard (`decode_image` in
  `lambda/detect/detect_cv.py`) rejects raw uploads over `MAX_INPUT_BYTES`
  (8 MB) and, separately, any image whose implied full-resolution pixel
  count exceeds `MAX_IMAGE_PIXELS` (50 megapixels) — checked cheaply via a
  header-only dimension probe before ever attempting a full decode, to
  guard against a small, highly-compressible upload decoding into a
  bitmap large enough to OOM-kill the Lambda. Separately, the Textract
  FORMS call used for gap-recovery hints (`textract_hints.py`, issue #7)
  goes through synchronous `AnalyzeDocument`, which caps document bytes at
  5 MB regardless of `MAX_INPUT_BYTES` — an upload between 5-8 MB would
  pass the pipeline's own guard but fail the Textract call. That failure
  is caught and logged, not raised (Textract is a pure enhancement, see
  "Architecture" above), so today it just means silently losing the
  gap-recovery pass on larger documents rather than an error response.
  Worth surfacing more visibly (or moving to the async, S3-backed
  `StartDocumentAnalysis` flow) if large scans turn out to be common.
- **IAM Identity Center migration:** see the TODO under "AWS account
  setup" (and its related ECR-permissions follow-up right below it) —
  replace the bootstrap IAM user with a real permission-set-based setup
  before this goes anywhere near a team/production account.
- **Handling documents very different from the sample set:** handwritten
  forms, non-Latin text, very low-DPI scans, and other layouts entirely
  unlike the dense printed appraisal forms in `samples/` are out of scope
  until one is actually encountered — the pipeline's constants are
  calibrated against this specific document family.
