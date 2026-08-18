# --- Detect Lambda (Python, container image) -------------------------------

data "aws_caller_identity" "current" {}

resource "aws_ecr_repository" "detect" {
  name                 = "${local.name_prefix}-detect"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# Grants the Lambda service permission to pull from this specific repo.
# Confirmed necessary the hard way: after renaming this repo (previously
# document-scanner-dev-detect-cv, which never needed this), Lambda's
# CreateFunction consistently failed with "AccessDeniedException: Lambda
# does not have permission to access the ECR image" - ruled out image
# staleness (rebuilt and re-pushed a fresh image, same failure) and IAM
# role identity (tested with a brand-new, never-before-used role, same
# failure) before landing on this as the actual fix. AWS's own same-
# account "implicit" ECR access for Lambda evidently isn't reliable
# enough to depend on; an explicit repository policy is the documented,
# supported way to grant this.
resource "aws_ecr_repository_policy" "detect_lambda_pull" {
  repository = aws_ecr_repository.detect.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "LambdaECRImageRetrievalPolicy"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action = [
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
      ]
      Condition = {
        StringLike = {
          "aws:sourceArn" = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:*"
        }
      }
    }]
  })
}

resource "null_resource" "build_and_push_detect_image" {
  triggers = {
    source_hash = sha256(join("", [
      filesha256("${path.module}/../lambda/detect/detect_cv.py"),
      filesha256("${path.module}/../lambda/detect/handler.py"),
      filesha256("${path.module}/../lambda/detect/textract_hints.py"),
      filesha256("${path.module}/../lambda/detect/requirements.txt"),
      filesha256("${path.module}/../lambda/detect/Dockerfile"),
    ]))
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../lambda/detect"
    command     = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.detect.repository_url}
      docker build --platform linux/arm64 --provenance=false --sbom=false -t ${aws_ecr_repository.detect.repository_url}:latest .
      docker push ${aws_ecr_repository.detect.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.detect]
}

data "aws_ecr_image" "detect" {
  repository_name = aws_ecr_repository.detect.name
  image_tag       = "latest"

  depends_on = [null_resource.build_and_push_detect_image]
}

resource "aws_iam_role" "detect_exec" {
  name = "${local.name_prefix}-detect-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "detect_basic_execution" {
  role       = aws_iam_role.detect_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Textract FORMS analysis feeds label-position hints into gap recovery
# (docs/algorithm-known-issues.md issue #7) - a pure enhancement over the
# CV pipeline's own detection, never a requirement (detect_cv.py fails
# open to CV-only results if this call fails for any reason).
resource "aws_iam_role_policy" "detect_textract_access" {
  name = "${local.name_prefix}-detect-textract-access"
  role = aws_iam_role.detect_exec.id

  # Textract API actions don't support resource-level ARNs; "*" is required.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["textract:AnalyzeDocument"]
      Resource = "*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "detect_lambda" {
  name              = "/aws/lambda/${local.name_prefix}-detect"
  retention_in_days = 14
}

resource "aws_lambda_function" "detect" {
  function_name = "${local.name_prefix}-detect"
  role          = aws_iam_role.detect_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.detect.repository_url}@${data.aws_ecr_image.detect.image_digest}"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 1024

  depends_on = [
    aws_cloudwatch_log_group.detect_lambda,
    aws_iam_role_policy_attachment.detect_basic_execution,
    aws_ecr_repository_policy.detect_lambda_pull,
  ]
}

resource "aws_apigatewayv2_integration" "detect" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.detect.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "detect" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /detect"
  target    = "integrations/${aws_apigatewayv2_integration.detect.id}"
}

resource "aws_lambda_permission" "apigw_invoke_detect" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.detect.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}
