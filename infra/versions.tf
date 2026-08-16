terraform {
  # >= 1.10 specifically because the S3 backend's `use_lockfile` argument
  # below doesn't exist before that version — on an older-but->=1.5 local
  # Terraform this would otherwise fail with a confusing backend-parse
  # error instead of a clear version-mismatch message.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Shared state (see infra/terraform_state.tf) so both a human deployer
  # and GitHub Actions CI see the same infrastructure — without this, CI
  # (an ephemeral runner with zero local state on every run) tries to
  # re-create everything that already exists. Credentials come from the
  # same source as the "aws" provider below (AWS_PROFILE / default chain),
  # not from anything hardcoded here.
  backend "s3" {
    bucket       = "document-scanner-terraform-state-268719686093"
    key          = "document-scanner/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true # native S3 locking (Terraform 1.10+), no DynamoDB needed
  }
}

provider "aws" {
  region = var.aws_region
}
