# --- Detect-CV Lambda (Python, container image) ----------------------------

resource "aws_ecr_repository" "detect_cv" {
  name                 = "${local.name_prefix}-detect-cv"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "null_resource" "build_and_push_detect_cv_image" {
  triggers = {
    source_hash = sha256(join("", [
      filesha256("${path.module}/../lambda/detect-cv/detect_cv.py"),
      filesha256("${path.module}/../lambda/detect-cv/handler.py"),
      filesha256("${path.module}/../lambda/detect-cv/requirements.txt"),
      filesha256("${path.module}/../lambda/detect-cv/Dockerfile"),
    ]))
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../lambda/detect-cv"
    command     = <<-EOT
      aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.detect_cv.repository_url}
      docker build --platform linux/arm64 -t ${aws_ecr_repository.detect_cv.repository_url}:latest .
      docker push ${aws_ecr_repository.detect_cv.repository_url}:latest
    EOT
  }

  depends_on = [aws_ecr_repository.detect_cv]
}

data "aws_ecr_image" "detect_cv" {
  repository_name = aws_ecr_repository.detect_cv.name
  image_tag       = "latest"

  depends_on = [null_resource.build_and_push_detect_cv_image]
}

resource "aws_iam_role" "detect_cv_exec" {
  name = "${local.name_prefix}-detect-cv-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "detect_cv_basic_execution" {
  role       = aws_iam_role.detect_cv_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "detect_cv_lambda" {
  name              = "/aws/lambda/${local.name_prefix}-detect-cv"
  retention_in_days = 14
}

resource "aws_lambda_function" "detect_cv" {
  function_name = "${local.name_prefix}-detect-cv"
  role          = aws_iam_role.detect_cv_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.detect_cv.repository_url}@${data.aws_ecr_image.detect_cv.image_digest}"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 1024

  depends_on = [
    aws_cloudwatch_log_group.detect_cv_lambda,
    aws_iam_role_policy_attachment.detect_cv_basic_execution,
  ]
}

resource "aws_apigatewayv2_integration" "detect_cv" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.detect_cv.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "detect_cv" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /detect"
  target    = "integrations/${aws_apigatewayv2_integration.detect_cv.id}"
}

resource "aws_lambda_permission" "apigw_invoke_detect_cv" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.detect_cv.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}
