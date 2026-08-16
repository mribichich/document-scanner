locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# --- Lambda package -------------------------------------------------------
# Go is compiled to a single "bootstrap" binary for the provided.al2023 runtime.

resource "null_resource" "build_detect_lambda" {
  # Always re-run, not just when main.go's content hash changes. Unlike
  # the CV Lambda's Docker image (pushed to ECR, a remote registry that
  # persists independently of Terraform state), the Go build output only
  # ever exists on whatever filesystem ran `go build` — on a fresh CI
  # checkout there's no bootstrap binary on disk yet, so a content-hash
  # trigger that happens to match already-applied state incorrectly skips
  # rebuilding it. Confirmed live: CI's data.archive_file.detect_lambda
  # failed with "could not archive missing file" because this local-exec
  # never ran on that fresh runner. `go build` is fast, so re-running it
  # every apply is cheap — but NOT byte-identical even when main.go is
  # unchanged (the Go toolchain embeds build metadata like timestamps/
  # paths by default), so expect aws_lambda_function.detect's
  # source_code_hash to show a diff and redeploy on every apply. That's a
  # minor inefficiency, not a correctness problem; revisit with
  # `-trimpath`/reproducible-build flags if it's ever worth avoiding.
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../lambda/detect"
    command     = "GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o bootstrap ."
  }
}

data "archive_file" "detect_lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/detect/bootstrap"
  output_path = "${path.module}/build/detect.zip"

  depends_on = [null_resource.build_detect_lambda]
}

# --- IAM --------------------------------------------------------------------

resource "aws_iam_role" "lambda_exec" {
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

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_textract" {
  name = "${local.name_prefix}-detect-textract"
  role = aws_iam_role.lambda_exec.id

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

# --- Lambda -------------------------------------------------------------

resource "aws_cloudwatch_log_group" "detect_lambda" {
  name              = "/aws/lambda/${local.name_prefix}-detect"
  retention_in_days = 14
}

resource "aws_lambda_function" "detect" {
  function_name = "${local.name_prefix}-detect"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "bootstrap"
  runtime       = "provided.al2023"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 512

  filename         = data.archive_file.detect_lambda.output_path
  source_code_hash = data.archive_file.detect_lambda.output_base64sha256

  depends_on = [
    aws_cloudwatch_log_group.detect_lambda,
    aws_iam_role_policy_attachment.lambda_basic_execution,
  ]
}

# --- API Gateway (HTTP API) ----------------------------------------------

resource "aws_apigatewayv2_api" "http_api" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
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
  route_key = "POST /detect-textract"
  target    = "integrations/${aws_apigatewayv2_integration.detect.id}"
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.detect.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}
