# document-scanner

Checkbox detection API. Given a document image, detects checkboxes and
classifies each as checked or unchecked. See `challenge.pdf` (pages 1-2) for
the full spec.

## API

```
POST /detect
POST /detect-textract
```

Both routes accept the same request and return the same response shape —
they're two independent implementations of the same contract (see
"Architecture" below for why there are two).

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
                        ,--POST /detect----------> Lambda "detect-cv" (Python, container image)
                        |                             --> classic CV pipeline (OpenCV: contours +
API Gateway (HTTP API) -+                                 geometric filtering + ink-density check)
                        |
                        `--POST /detect-textract-> Lambda "detect" (Go, provided.al2023)
                                                      --> AWS Textract (AnalyzeDocument, FeatureTypes=FORMS)
```

`POST /detect` is the default, actively-developed implementation: a
classic computer-vision pipeline running in a Python Lambda
(`lambda/detect-cv/`, deployed as a container image). `POST /detect-textract`
is the original Go/Textract implementation — unchanged except for its route
path — kept deployed for side-by-side comparison rather than deleted. See
`docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md` for the
full rationale, including the two concrete Textract failure cases (a false
positive from a stray scratch mark, a false negative under a watermark)
that motivated building the CV pipeline.

**CV pipeline** (`lambda/detect-cv/detect_cv.py`): grayscale + threshold ->
`findContours` -> geometric filtering (size, aspect ratio, corner count,
rectangularity) to find checkbox-shaped candidates -> IoU-based
deduplication of nested contours -> per-box classification via ink-ratio +
a containment check (a mark is "checked" only if it's sized to fit inside
the box, not a fragment of a longer stroke passing through it). All
tunable constants live at the top of that file. `handler.py`'s error
responses never leak internal detail to the caller: a bad request body
maps to a generic `400`, and any other unexpected exception maps to a
generic `500` with a fixed `{"error": "internal error"}` body — the full
exception is logged via `logging.exception` first, which Lambda
automatically ships to CloudWatch Logs, so the detail is still there for
debugging without ever reaching the response.

**Textract pipeline** (`lambda/detect/main.go`): parses the uploaded image
(raw body or `multipart/form-data`), calls Textract's `AnalyzeDocument`
with the `FORMS` feature type, and maps every `SELECTION_ELEMENT` block
(checkboxes/radio buttons) it returns into a pixel-coordinate `bbox` +
`is_checked` entry — converting Textract's relative (0-1) bounding boxes
using the source image's actual pixel dimensions.

- `infra/` — Terraform: API Gateway HTTP API, both Lambda functions (one
  zip-deployed, one container-image), their IAM roles (the Textract
  Lambda's includes Textract access; the CV Lambda's is CloudWatch-Logs-only),
  the CV Lambda's ECR repository, CloudWatch log groups.
- `lambda/detect/` — Go Lambda source (`main.go`) for `/detect-textract`.
  Terraform compiles this to a `bootstrap` binary and zips it before
  deploy.
- `lambda/detect-cv/` — Python Lambda source for `/detect`:
  `detect_cv.py` (core detection algorithm), `handler.py` (Lambda entry
  point), `cli.py` (local dev/tuning tool, not deployed), `Dockerfile`
  (container image — required because `opencv-python-headless` needs
  native shared libraries, so this Lambda is `package_type = "Image"`
  rather than zip-deployed like the Go one), `tests/` (pytest suite).
- `samples/` — 4 real appraisal-document images (extracted from
  `challenge.pdf`'s embedded sample images) for testing detection quality.
  `samples/results/` holds saved raw JSON responses and annotated
  (bbox-overlaid) PNGs from the last verified test run of each
  implementation — see "Testing" below.
- `scripts/annotate_detections.py` — draws detected boxes over the sample
  images (green = checked, red = unchecked) so Textract results can be
  reviewed visually. (The CV pipeline's `cli.py` draws its own annotated
  output directly — see "Local CV development loop" below.)

## Prerequisites

This project pins tool versions with [asdf](https://asdf-vm.com/). Install
asdf itself first, then:

```bash
asdf plugin add golang
asdf plugin add terraform
asdf plugin add awscli
asdf plugin add python
asdf plugin add github-cli
asdf install
```

This installs the exact Go, Terraform, AWS CLI, Python, and GitHub CLI
versions listed in `.tool-versions`. GitHub CLI (`gh`) isn't required to
develop or deploy this project — it's only used for inspecting GitHub
Actions runs (see "CI/CD" below) and is optional.

### Docker

Building and pushing the CV Lambda's container image (`lambda/detect-cv/`)
requires [Docker](https://docs.docker.com/get-docker/) (e.g. Docker
Desktop) to be installed and running locally — Terraform shells out to
`docker build`/`docker push` as part of `terraform apply` (see "Deploy"
below). Docker is **not** asdf-managed: asdf pins language/CLI tool
versions, but a container runtime is a different kind of dependency, so
install it the normal way for your OS.

You don't need Docker just to run the CV algorithm locally, though — the
`cli.py` dev loop (see "Local CV development loop" under Testing) only
needs a plain Python virtualenv.

### Lambda (Go) dependencies

`lambda/detect/go.mod` and `go.sum` are committed, so you normally don't
need to do anything — `go build` (which Terraform runs for you on every
`apply`) resolves and downloads whatever `go.sum` pins automatically. If
you want to fetch them ahead of time (e.g. to work offline afterwards) or
just sanity-check the module:

```bash
cd lambda/detect
go mod download
go build -o /dev/null .   # compiles for your local OS/arch, just to check for errors
```

**Adding or upgrading a dependency:**

```bash
cd lambda/detect
go get <module>[@version]   # e.g. go get github.com/aws/aws-sdk-go-v2/service/textract
go mod tidy                 # cleans up go.mod/go.sum, marks direct vs indirect correctly
```

Commit the updated `go.mod` and `go.sum` — Terraform's build step relies on
them being present and consistent; it doesn't run `go mod tidy` itself.

### Lambda (Python CV) dependencies

`lambda/detect-cv/requirements.txt` (runtime: `opencv-python-headless`,
`numpy`) and `requirements-dev.txt` (adds `pytest`) are committed. For
local development (running `cli.py` or the test suite), set up a
virtualenv once:

```bash
cd lambda/detect-cv
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
```

This venv is only needed for local dev/testing — it's not used at deploy
time. The deployed Lambda instead builds `requirements.txt` into a
container image via `Dockerfile` (see "Docker" above and "Deploy" below).

Run the tests with `venv/bin/python3 -m pytest` from `lambda/detect-cv/`.

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
# CV Lambda's container image)
aws iam put-user-policy \
  --user-name document-scanner-deployer \
  --policy-name document-scanner-deploy \
  --policy-document file://infra/bootstrap-iam-policy.json
```

This is the *only* step here that needs root/admin credentials. Everything
below runs as `document-scanner-deployer`.

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

> **TODO:** this per-person IAM user + inline policy is a shortcut
> acceptable for a single-developer/personal account. It does not scale
> to a real team: every new developer would mean provisioning another
> long-lived IAM user by hand. For a production account, replace this
> with **IAM Identity Center (SSO) + a permission set** granting the
> `infra/bootstrap-iam-policy.json` permissions — new developers get
> assigned to the permission set centrally, authenticate with their own
> SSO identity via `aws sso login`, and there are no long-lived
> credentials to create or rotate per person. CI/CD should similarly move
> to an OIDC-federated role rather than a static access key.

> **Related follow-up:** the `EcrManageDetectCvRepo` statement in
> `infra/bootstrap-iam-policy.json` uses `"Action": "ecr:*"` (still scoped
> to just this project's own repo ARN, not account-wide) rather than an
> explicit action list like every other statement in that file. That's a
> deliberate compromise, not an oversight: AWS caps inline IAM user
> policies at 2048 bytes, and the fully-enumerated ECR action list didn't
> fit alongside everything else already in this policy. A cleaner fix is
> converting this whole file from an inline user policy to a
> customer-managed policy (6144-byte limit, room for full enumeration) —
> which is really the same underlying problem as the IAM Identity Center
> TODO above: this bootstrap-deploy-user pattern is already straining at
> its edges for a single-developer setup.

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

Terraform will cross-compile the Go Lambda (`GOOS=linux GOARCH=arm64`) and
also build + push the CV Lambda's container image (`docker build
--platform linux/arm64 ...` against `lambda/detect-cv/Dockerfile`, then
`docker push` to a per-project ECR repo it creates) as part of the apply —
Docker must be running locally for this step (see "Docker" under
Prerequisites). On success it prints `detect_endpoint` (the CV pipeline)
and `detect_textract_endpoint` (the Go/Textract pipeline), the full URLs
of the two deployed routes.

> **Gotcha:** right after creating/updating the IAM user's inline policy
> (step 1 above), the very first `terraform apply` may fail with
> `AccessDeniedException` on read-only calls (e.g.
> `lambda:GetFunctionCodeSigningConfig`, `logs:DescribeLogGroups`) even
> though the policy is correct — IAM permission changes can take a minute
> or two to propagate. Just re-run `terraform apply`.

> **Gotcha:** if the Docker build/push step fails with something like
> `InvalidParameterValueException: image manifest ... not supported`,
> it's because some Docker Desktop versions' `buildx` builder emits
> provenance/SBOM attestation manifests by default that Lambda's container
> image support rejects. `infra/detect_cv.tf` already passes
> `--provenance=false --sbom=false` to `docker build` to avoid this, but if
> you hit an equivalent error with a different Docker setup/version, that
> flag pair is the fix to look for.

## CI/CD

`.github/workflows/deploy.yml` runs on every push and pull request against
`main`:

- **`test` job** (always runs): compiles the Go Lambda, runs the full
  `lambda/detect-cv` pytest suite, and checks Terraform formatting.
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

### Local CV development loop (no AWS required)

The fastest way to iterate on the CV algorithm itself is `cli.py`, which
runs `detect_cv.py` directly against local files — no Lambda, no API
Gateway, no deploy:

```bash
cd lambda/detect-cv
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt   # one-time, see Prerequisites
venv/bin/python3 cli.py <image_or_folder>
```

For each image, it writes into a `results/` subfolder alongside the
input:

- `<name>-cv.json` — the raw `{"boxes": [...]}` response
- `<name>-cv-annotated.png` — the boxes drawn directly on the image
  (green outline = `is_checked: true`, red = `false`), so you can eyeball
  correctness immediately without a separate visualization step

E.g. `venv/bin/python3 cli.py ../../samples` processes all 4 sample images
and writes `samples/results/appraisal-{1..4}-cv.{json,png}`. This is the
loop used to calibrate the constants at the top of `detect_cv.py`
(`MIN_BOX_SIZE`, `MAX_BOX_SIZE`, `MIN_EXTENT_RATIO`, etc.) — run it,
eyeball the annotated PNGs, adjust a constant, repeat.

The pytest suite (`tests/`) covers the same code with unit/integration
tests; run it with `venv/bin/python3 -m pytest` from `lambda/detect-cv/`.

### Quick check with any image

Either endpoint accepts the same request shape — swap `detect_endpoint`
for `detect_textract_endpoint` to hit the Go/Textract implementation
instead:

```bash
curl -X POST "$(terraform -chdir=infra output -raw detect_endpoint)" \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"

curl -X POST "$(terraform -chdir=infra output -raw detect_textract_endpoint)" \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

### Test against the live URL directly

If you just want to hit the currently-deployed dev environment without
running Terraform yourself, use its fixed endpoints:

```bash
curl -X POST https://quslj98to1.execute-api.us-east-1.amazonaws.com/detect \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"

curl -X POST https://quslj98to1.execute-api.us-east-1.amazonaws.com/detect-textract \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

This base URL is whatever `terraform apply` last produced in this
account — it changes if the API Gateway resource is ever destroyed and
recreated, so treat it as a convenience snapshot, not a stable contract.
The authoritative values are always `terraform -chdir=infra output
detect_endpoint` and `terraform -chdir=infra output detect_textract_endpoint`.

### Test against a folder of images

`scripts/call_detect_api.sh` sends every `.png`/`.jpg`/`.jpeg` in a folder
to whichever endpoint you give it, prints a one-line summary per file
(HTTP status, boxes detected, checked/unchecked counts), and saves each
raw JSON response into a `results/` subfolder it creates alongside the
images:

```bash
./scripts/call_detect_api.sh <folder> [endpoint_url]
```

`endpoint_url` is optional — it defaults to
`terraform -chdir=infra output -raw detect_endpoint` (the CV pipeline). To
target the Textract pipeline instead, pass it explicitly:

```bash
./scripts/call_detect_api.sh samples "$(terraform -chdir=infra output -raw detect_endpoint)"
./scripts/call_detect_api.sh samples "$(terraform -chdir=infra output -raw detect_textract_endpoint)"
```

Works against any folder, not just `samples/`.

> **Gotcha:** the script always writes to `results/<name>.json` regardless
> of which endpoint you point it at — it doesn't know which implementation
> produced the response, unlike `cli.py`'s `-cv` suffix. Running it twice
> against the same folder with different endpoints overwrites the first
> run's output with the second. If you want to keep both sets of results,
> copy the `results/` folder aside between runs (or diff it) before
> switching endpoints.

### Test against the sample appraisal documents

`samples/appraisal-1.png` through `appraisal-4.png` are the 4 real sample
images from the challenge (extracted from `challenge.pdf`'s embedded
images — actual Fannie Mae/Freddie Mac appraisal forms full of checkboxes,
both filled and empty):

```bash
./scripts/call_detect_api.sh samples                                                              # CV, /detect
./scripts/call_detect_api.sh samples "$(terraform -chdir=infra output -raw detect_textract_endpoint)"  # Textract, /detect-textract
```

(Remember the overwrite gotcha above if you run both back-to-back and want
to keep both sets of raw JSON.) Or by hand, one file at a time:

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

Known-good results as of the last verified deploy of each implementation
(these are dense, multi-page appraisal forms; small shifts are normal —
Textract's numbers may drift slightly as its underlying model updates, CV
numbers only change if `detect_cv.py`'s constants are recalibrated):

**CV pipeline** (`/detect`, from `lambda/detect-cv/cli.py` — see
"Local CV development loop" above):

| Sample            | Boxes detected | Checked | Unchecked |
| ------------------ | -------------- | ------- | --------- |
| appraisal-1.png    | 118            | 33      | 85        |
| appraisal-2.png    | 41             | 17      | 24        |
| appraisal-3.png    | 30             | 8       | 22        |
| appraisal-4.png    | 77             | 25      | 52        |

**Textract pipeline** (`/detect-textract`, unchanged from the original
implementation):

| Sample            | Boxes detected | Checked | Unchecked |
| ------------------ | -------------- | ------- | --------- |
| appraisal-1.png    | 98             | 33      | 65        |
| appraisal-2.png    | 39             | 17      | 22        |
| appraisal-3.png    | 48             | 12      | 36        |
| appraisal-4.png    | 71             | 26      | 45        |

These box counts aren't expected to match between the two implementations
(different algorithms, tuned independently) — see "Visualizing detections"
below and `docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md`
for why the CV pipeline finds more boxes on some pages and fewer on
others.

Expected response shape (same for both endpoints):

```json
{
  "boxes": [
    { "bbox": [489, 1455, 544, 1502], "is_checked": true },
    { "bbox": [489, 1405, 544, 1452], "is_checked": false }
  ]
}
```

Raw responses from the last verified run of each implementation are saved
at `samples/results/appraisal-*.json` (Textract) and
`samples/results/appraisal-*-cv.json` (CV) — not just left in `/tmp` — so
they can be diffed against or inspected later without re-running anything.

### Visualizing detections

The JSON alone is hard to sanity-check.

For the **CV pipeline**, `cli.py` (see "Local CV development loop" above)
already writes `samples/results/appraisal-{1..4}-cv-annotated.png` as part
of every run — no separate step needed.

For the **Textract pipeline**, `scripts/annotate_detections.py` draws
every detected box over its source image (green outline =
`is_checked: true`, red = `false`) the same way:

```bash
python3 -m venv /tmp/annotate-venv && /tmp/annotate-venv/bin/pip install --quiet Pillow
/tmp/annotate-venv/bin/python3 scripts/annotate_detections.py samples samples/results
```

Outputs `samples/results/appraisal-{1..4}-annotated.png`.

On the last verified visual review of the CV pipeline (same bar as the
original Textract review — eyeball every box against the source image):
one of the two concrete failure cases that motivated building the CV
pipeline is fixed, and the other has changed shape rather than
disappeared — see "appraisal-2.png scratch line: missed detection, not a
correct classification" under "Next steps" for the detail. On
`appraisal-4.png`, the "Utilities" checkbox grid (Electricity/Gas/Water/Sanitary
Sewer) that Textract dropped entirely under a diagonal watermark is now
fully detected, with each box correctly classified. Overall box placement
is tight and classification matches the visible marks on both reviewed
pages.

One known gap: on `appraisal-1.png`, calibration converged on 118
detected boxes against an estimate of ~125 expected (per a similar prior
manual analysis of that page). Investigation during calibration found no
further adjustment to the existing tunable constants that closes this gap
without also reintroducing other false positives/negatives — closing it
fully would need an algorithmic addition (e.g. explicit suppression of
table gridlines that coincidentally form checkbox-like shapes), which
wasn't built speculatively. Documented here as a known, investigated gap,
not a silently accepted one.

## Viewing logs

```bash
# Textract Lambda (/detect-textract)
aws logs tail /aws/lambda/document-scanner-dev-detect --profile document-scanner --follow

# CV Lambda (/detect)
aws logs tail /aws/lambda/document-scanner-dev-detect-cv --profile document-scanner --follow
```

(Swap in `terraform -chdir=infra output -raw lambda_function_name` for the
first command if the environment/function name ever changes from the
default `dev` — that output currently only covers the Textract Lambda;
the CV Lambda's log group name follows the same `document-scanner-<env>-detect-cv`
pattern but isn't yet exposed as its own Terraform output.)

## Tear down

```bash
cd infra
terraform destroy
```

(This also deletes the CV Lambda's ECR repository and the container image
in it — `force_delete = true` on `aws_ecr_repository.detect_cv` so
`terraform destroy` doesn't get blocked by leftover images.)

## Next steps

Both detection implementations are wired in, deployed behind the same API
Gateway, and validated against all 4 sample images, including a visual
accuracy review of each (see "Visualizing detections" above). The CV
pipeline fixes one of the two concrete Textract failure cases that
motivated building it (the appraisal-4 watermarked Utilities-grid false
negatives); the appraisal-2 scratch-line case is not fixed so much as
changed into a different defect — see the first item below. Remaining
ideas, roughly in priority order:

- **appraisal-2.png scratch line: missed detection, not a correct
  classification.** The original Textract failure was a false positive:
  the stray diagonal scratch line crossing the "No Zoning" checkbox (around
  x=718, y=503 in the 1586x846 image) made Textract read it as
  `is_checked: true`. The CV pipeline does *not* fix this by correctly
  recognizing the mark as unrelated to the box and classifying it
  red/unchecked — verified directly, that box does not appear in
  `detect_checkboxes`'s output at all. What actually happens: during
  candidate detection, the scratch line's ink is 8-connected to the
  checkbox's own drawn border, so `cv2.findContours` returns one fused
  contour for "border + scratch line" whose bounding box is roughly
  859x31px (versus this document's ~24x24px checkboxes) — far outside
  `MAX_BOX_SIZE`, so it's discarded before ever reaching classification.
  The box simply isn't detected; this is a missed checkbox (false
  negative), a different defect than the false positive it replaced, not
  an absence of one. Fixing it would need candidate detection to be more
  robust to a mark fusing with a box's border (e.g. morphological opening
  to break thin connecting strokes before contour extraction) — not yet
  built, tracked here as a known, verified gap rather than a silently
  assumed fix.
- **Document-size / DPI generalization:** the CV pipeline's tunable
  constants (`MIN_BOX_SIZE`, `MAX_BOX_SIZE`, `MIN_EXTENT_RATIO`, etc., all
  at the top of `lambda/detect-cv/detect_cv.py`) are calibrated in pixels
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
- **appraisal-1.png box count:** see "Visualizing detections" above —
  calibration landed at 118 detected boxes against an estimated ~125
  expected, with no further tunable-constant fix found; closing this gap
  would need an algorithmic change (e.g. explicit table-gridline
  suppression), tracked as a known gap rather than silently accepted.
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
  problem at all — see "appraisal-2.png scratch line" above; that mark
  never reaches classification in the first place. Revisit the Hough-line
  fallback if a document with a harder version of the "unrelated mark near
  a checkbox" problem is encountered. **Specific, currently-untested
  gap** (found via an audit against `docs/chatgpt.md`): `is_checked()`
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
- **Retiring `/detect-textract`:** deliberately deferred — see
  `docs/superpowers/specs/2026-08-14-cv-checkbox-detection-design.md`
  ("Open questions"). Keep both endpoints live until there's enough
  side-by-side comparison data (beyond the 4 samples) to justify dropping
  one.
- **Size limit (Textract path only):** Textract's synchronous
  `AnalyzeDocument` caps document bytes at 5 MB; large/high-DPI scans over
  that need the async, S3-backed Textract flow (`StartDocumentAnalysis` +
  `GetDocumentAnalysis`) instead. The CV pipeline has no such API-level
  contract limit, but `decode_image` in `lambda/detect-cv/detect_cv.py`
  does reject raw uploads over `MAX_INPUT_BYTES` (8 MB) and, separately,
  any image whose implied full-resolution pixel count exceeds
  `MAX_IMAGE_PIXELS` (50 megapixels) — checked cheaply via an 8x-downscaled
  probe decode before ever attempting a full-resolution one — to guard
  against a small, highly-compressible upload decoding into a bitmap large
  enough to OOM-kill the Lambda.
- **IAM Identity Center migration:** see the TODO under "AWS account
  setup" (and its related ECR-permissions follow-up right below it) —
  replace the bootstrap IAM user with a real permission-set-based setup
  before this goes anywhere near a team/production account.
- **Handling documents very different from the sample set:** handwritten
  forms, non-Latin text, very low-DPI scans, and other layouts entirely
  unlike the dense printed appraisal forms in `samples/` are out of scope
  until one is actually encountered — both implementations are tuned
  against (Textract's model) or calibrated against (the CV pipeline's
  constants) this specific document family.
