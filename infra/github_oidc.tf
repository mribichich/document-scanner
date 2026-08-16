# --- GitHub Actions OIDC deploy role ---------------------------------------
#
# Lets GitHub Actions workflows assume an AWS role directly via OIDC token
# federation, with no long-lived AWS credentials stored as GitHub secrets.
# Trust is scoped to this exact repo and the main branch only (see the
# `sub` condition below) — a workflow run from a fork or a non-main branch
# cannot assume this role.

variable "github_repo" {
  description = "GitHub repo allowed to assume the deploy role, as \"owner/repo\""
  type        = string
  default     = "mribichich/document-scanner"
}

# GitHub's OIDC token `sub` claim is NOT the plain "owner/repo" name — it's
# "owner@<owner_id>/repo@<repo_id>", using GitHub's permanent numeric IDs
# for the account and repository rather than their (renameable/
# transferable) names. Confirmed empirically by decoding a real token from
# this repo's own Actions run (a plain-name trust condition was tried
# first and consistently rejected by AWS with "Not authorized to perform
# sts:AssumeRoleWithWebIdentity" — the token's actual sub claim was
# "repo:mribichich@5748554/document-scanner@1333334483:ref:refs/heads/main").
# Using the ID-qualified form here (rather than a wildcard around the
# names) keeps the trust condition an exact match — a wildcard like
# "mribichich*" would also match unrelated accounts whose name happens to
# start with the same prefix.
variable "github_owner_id" {
  description = "GitHub numeric user/org ID for github_repo's owner (permanent, survives renames)"
  type        = string
  default     = "5748554"
}

variable "github_repo_id" {
  description = "GitHub numeric repository ID for github_repo (permanent, survives renames/transfers)"
  type        = string
  default     = "1333334483"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # GitHub's OIDC token-signing certificate chain. AWS validates the actual
  # TLS certificate presented by the provider at assume-role time, not just
  # this list, but the API still requires it. Both GitHub's current
  # intermediate and the DigiCert root it chains to are included so a future
  # CA rotation on GitHub's side doesn't silently break this.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

resource "aws_iam_role" "github_actions_deploy" {
  name = "${local.name_prefix}-github-actions-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github_actions.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${split("/", var.github_repo)[0]}@${var.github_owner_id}/${split("/", var.github_repo)[1]}@${var.github_repo_id}:ref:refs/heads/main"
        }
      }
    }]
  })
}

# Same deploy permissions as the human bootstrap user (infra/bootstrap-iam-policy.json)
# — CI needs to run the exact same terraform apply a human deployer would.
resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "${local.name_prefix}-github-actions-deploy"
  role   = aws_iam_role.github_actions_deploy.id
  policy = file("${path.module}/bootstrap-iam-policy.json")
}

output "github_actions_deploy_role_arn" {
  description = "Role ARN for the GitHub Actions workflow to assume via OIDC"
  value       = aws_iam_role.github_actions_deploy.arn
}
