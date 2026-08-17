output "api_base_url" {
  description = "Base URL of the deployed HTTP API"
  value       = aws_apigatewayv2_api.http_api.api_endpoint
}

output "detect_endpoint" {
  description = "Full URL of the POST /detect endpoint"
  value       = "${aws_apigatewayv2_api.http_api.api_endpoint}/detect"
}

output "lambda_function_name" {
  description = "Name of the deployed Lambda function"
  value       = aws_lambda_function.detect.function_name
}
