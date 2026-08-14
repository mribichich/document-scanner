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
API Gateway (HTTP API) --POST /detect--> Lambda (Go, provided.al2023) --> AWS Textract (AnalyzeDocument, FeatureTypes=FORMS)
```

The Lambda parses the uploaded image (raw body or `multipart/form-data`),
calls Textract's `AnalyzeDocument` with the `FORMS` feature type, and maps
every `SELECTION_ELEMENT` block (checkboxes/radio buttons) it returns into
a pixel-coordinate `bbox` + `is_checked` entry — converting Textract's
relative (0-1) bounding boxes using the source image's actual pixel
dimensions.

- `infra/` — Terraform: API Gateway HTTP API, Lambda function, IAM role
  (including Textract access), CloudWatch log group.
- `lambda/detect/` — Go Lambda source (`main.go`). Terraform compiles this
  to a `bootstrap` binary and zips it before deploy.
- `samples/` — 4 real appraisal-document images (extracted from
  `challenge.pdf`'s embedded sample images) for testing detection quality.
  `samples/results/` holds saved raw JSON responses and annotated
  (bbox-overlaid) PNGs from the last verified test run — see "Testing"
  below.
- `scripts/annotate_detections.py` — draws detected boxes over the sample
  images (green = checked, red = unchecked) so results can be reviewed
  visually.

## Prerequisites

This project pins tool versions with [asdf](https://asdf-vm.com/). Install
asdf itself first, then:

```bash
asdf plugin add golang
asdf plugin add terraform
asdf plugin add awscli
asdf install
```

This installs the exact Go, Terraform, and AWS CLI versions listed in
`.tool-versions`.

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
# Lambda's own execution role, CloudWatch Logs, Textract)
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

## Deploy

```bash
cd infra
export AWS_PROFILE=document-scanner-tf   # skip if using a static access key / CI
terraform init
terraform plan
terraform apply
```

Terraform will cross-compile the Go Lambda (`GOOS=linux GOARCH=arm64`) as
part of the apply. On success it prints `detect_endpoint`, the full URL of
the deployed `POST /detect` route.

> **Gotcha:** right after creating/updating the IAM user's inline policy
> (step 1 above), the very first `terraform apply` may fail with
> `AccessDeniedException` on read-only calls (e.g.
> `lambda:GetFunctionCodeSigningConfig`, `logs:DescribeLogGroups`) even
> though the policy is correct — IAM permission changes can take a minute
> or two to propagate. Just re-run `terraform apply`.

## Testing

### Quick check with any image

```bash
curl -X POST "$(terraform -chdir=infra output -raw detect_endpoint)" \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

### Test against the live URL directly

If you just want to hit the currently-deployed dev environment without
running Terraform yourself, use its fixed endpoint:

```bash
curl -X POST https://quslj98to1.execute-api.us-east-1.amazonaws.com/detect \
  -F "file=@/path/to/some-document.jpg" \
  -w "\nHTTP %{http_code}\n"
```

This URL is whatever `terraform apply` last produced in this account — it
changes if the API Gateway resource is ever destroyed and recreated, so
treat it as a convenience snapshot, not a stable contract. The
authoritative value is always `terraform -chdir=infra output detect_endpoint`.

### Test against a folder of images

`scripts/call_detect_api.sh` sends every `.png`/`.jpg`/`.jpeg` in a folder
to `POST /detect`, prints a one-line summary per file (HTTP status, boxes
detected, checked/unchecked counts), and saves each raw JSON response into
a `results/` subfolder it creates alongside the images:

```bash
./scripts/call_detect_api.sh <folder> [endpoint_url]
```

`endpoint_url` is optional — it defaults to
`terraform -chdir=infra output -raw detect_endpoint`. Works against any
folder, not just `samples/`.

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

Known-good result as of the last verified deploy (numbers will shift
slightly as Textract's model updates, but should stay in this ballpark —
these are dense, multi-page appraisal forms):

| Sample            | Boxes detected | Checked | Unchecked |
| ------------------ | -------------- | ------- | --------- |
| appraisal-1.png    | 98             | 33      | 65        |
| appraisal-2.png    | 39             | 17      | 22        |
| appraisal-3.png    | 48             | 12      | 36        |
| appraisal-4.png    | 71             | 26      | 45        |

Expected response shape (real detections, not the earlier stub):

```json
{
  "boxes": [
    { "bbox": [489, 1455, 544, 1502], "is_checked": true },
    { "bbox": [489, 1405, 544, 1452], "is_checked": false }
  ]
}
```

Raw responses from the last verified run are saved at
`samples/results/appraisal-*.json` (not just left in `/tmp`) so they can be
diffed against or inspected later without re-running anything.

### Visualizing detections

The JSON alone is hard to sanity-check. `scripts/annotate_detections.py`
draws every detected box over its source image (green outline =
`is_checked: true`, red = `false`) so you can eyeball accuracy directly:

```bash
python3 -m venv /tmp/annotate-venv && /tmp/annotate-venv/bin/pip install --quiet Pillow
/tmp/annotate-venv/bin/python3 scripts/annotate_detections.py samples samples/results
```

Outputs `samples/results/appraisal-{1..4}-annotated.png`. On the last
verified run, every checkbox on all 4 sample forms was detected with a
tightly-fit box, and every green/red classification matched the visible
`X` marks — no observed false positives or misses.

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

## Next steps

Textract-based detection is wired in and validated against all 4 sample
images, including a visual accuracy review (see "Visualizing detections"
above) — every checkbox on all 4 forms was detected with a tightly-fit box
and correctly classified, no observed false positives or misses. Remaining
ideas, roughly in priority order:

- **CV fallback/refinement:** if Textract misses non-standard checkbox
  glyphs on other document types, consider a classic computer-vision pass
  (contour detection + fill-ratio thresholding) as a refinement layer —
  likely a separate Python Lambda (container image), since OpenCV
  bindings are far more mature there than in Go (`gocv` needs CGO + native
  libs, painful on `provided.al2023`).
- **Size limit:** Textract's synchronous `AnalyzeDocument` caps document
  bytes at 5 MB; large/high-DPI scans over that need the async, S3-backed
  Textract flow (`StartDocumentAnalysis` + `GetDocumentAnalysis`) instead.
- **IAM Identity Center migration:** see the TODO under "AWS account
  setup" — replace the bootstrap IAM user with a real permission-set-based
  setup before this goes anywhere near a team/production account.
