# --- Terraform remote state --------------------------------------------
#
# Shared state so both the human deployer and GitHub Actions CI see the
# same infrastructure. Without this, CI (an ephemeral runner starting
# with zero local state on every run) tries to re-create everything that
# already exists — confirmed live: the first CI deploy attempt failed
# with "EntityAlreadyExists"/"ResourceAlreadyExistsException" across
# multiple resources that were already applied locally.
#
# Bootstrapping note: this bucket has to exist and be populated with this
# module's state before the `backend "s3"` block in versions.tf can be
# activated — chicken-and-egg. Sequence used to set this up: apply this
# file alone with local state (creates the bucket), then add the backend
# block and run `terraform init -migrate-state` to move local state in.

resource "aws_s3_bucket" "terraform_state" {
  bucket = "document-scanner-terraform-state-268719686093"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
