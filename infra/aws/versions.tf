terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local for now (terraform.tfstate next to this file, git-ignored). It holds no secrets, but
  # losing it means importing everything by hand: keep a copy, or move it to an S3 backend later.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "mon-ecole"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
