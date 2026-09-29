variable "region" {
  description = "AWS region; keep it next to the Supabase project (eu-west-3 = Paris)."
  type        = string
  default     = "eu-west-3"
}

variable "environment" {
  description = "Environment name; must match the GitHub environment that deploys to it."
  type        = string
  default     = "production"
}

variable "instance_type" {
  description = "ARM (Graviton) instance on the AWS Free plan: t4g.micro (1 GB) or t4g.small (2 GB)."
  type        = string
  default     = "t4g.small"
}

variable "api_domain" {
  description = "API hostname, e.g. api.example.org (point its DNS A record at the elastic IP). Empty = api.<ip>.sslip.io."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "Repository whose GitHub Actions may deploy (owner/name)."
  type        = string
  default     = "FOFANA459-2023/Mon-Ecole-Backend"
}

variable "create_github_oidc_provider" {
  description = "Create the account-wide GitHub Actions OIDC provider (false if it already exists in this account)."
  type        = bool
  default     = true
}
