locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# --- API Gateway (HTTP API) ----------------------------------------------
#
# Shared across every Lambda this API routes to (currently just the CV
# detection Lambda, detect.tf) - not specific to any one implementation.

resource "aws_apigatewayv2_api" "http_api" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
}
