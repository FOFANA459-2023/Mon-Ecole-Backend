output "api_url" {
  description = "Put \"<api_url>/api/v1\" in the frontend Worker's VITE_API_URL build variable."
  value       = "https://${local.api_host}"
}

output "public_ip" {
  description = "Point the API domain's DNS A record here once you have one."
  value       = aws_eip.api.public_ip
}

output "media_bucket" {
  description = "AWS_STORAGE_BUCKET_NAME in the app settings."
  value       = aws_s3_bucket.media.bucket
}

output "app_env_parameter" {
  description = "SSM parameter (SecureString) holding the app settings."
  value       = "${local.ssm_path}/app-env"
}

output "github_environment_variables" {
  description = "Variables of the GitHub environment that deploys here (Mon-Ecole-Backend → Settings → Environments)."
  value = {
    AWS_REGION          = var.region
    AWS_DEPLOY_ROLE_ARN = aws_iam_role.deploy.arn
    ECR_REPOSITORY      = aws_ecr_repository.backend.repository_url
    EC2_INSTANCE_ID     = aws_instance.api.id
  }
}
