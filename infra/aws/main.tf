# Free-tier production: one Graviton EC2 instance runs Caddy (HTTPS), the API, the Celery worker and beat,
# and Valkey with Docker Compose (see deploy/ec2). PostgreSQL is Supabase; files go to a private S3 bucket.
# The planned ECS Fargate + ALB + ElastiCache setup replaces the instance later without app changes.

locals {
  name      = "mon-ecole-${var.environment}"
  api_host  = var.api_domain != "" ? var.api_domain : "api.${replace(aws_eip.api.public_ip, ".", "-")}.sslip.io"
  ssm_path  = "/mon-ecole/${var.environment}"
  ssm_arn   = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_path}"
  oidc_host = "token.actions.githubusercontent.com"
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# --- Network -------------------------------------------------------------------------------------------

resource "aws_security_group" "api" {
  name        = local.name
  description = "Mon Ecole API: HTTPS in (Caddy); no SSH, shell access goes through SSM Session Manager"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.api.id
  description       = "HTTP (certificate challenges, redirect to HTTPS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = toset(["tcp", "udp"]) # udp = HTTP/3
  security_group_id = aws_security_group.api.id
  description       = "HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = each.value
  from_port         = 443
  to_port           = 443
}

# Supabase, ECR, SSM and EmailJS have no fixed addresses to allow-list; nothing listens on the way out.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.api.id
  description       = "Outbound: Supabase, S3, ECR, SSM, EmailJS, Sentry"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# A fixed address, so the API hostname survives stop/start.
resource "aws_eip" "api" {
  domain = "vpc"
  tags   = { Name = local.name }
}

resource "aws_eip_association" "api" {
  instance_id   = aws_instance.api.id
  allocation_id = aws_eip.api.id
}

# --- Server --------------------------------------------------------------------------------------------

resource "aws_instance" "api" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.api.id]
  iam_instance_profile   = aws_iam_instance_profile.api.name
  user_data              = file("${path.module}/user-data.sh")

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the instance role through IMDSv2 (S3 access)
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  credit_specification {
    cpu_credits = "standard" # never bill for CPU bursts beyond the earned credits
  }

  tags = { Name = local.name }

  lifecycle {
    # A newer Amazon Linux image must not replace the running server; recreate it deliberately.
    ignore_changes = [ami, user_data]
  }
}

# --- Container registry and files ------------------------------------------------------------------------

resource "aws_ecr_repository" "backend" {
  name                 = "mon-ecole-backend"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images (older tags are still on GHCR for rollbacks)"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "media" {
  bucket_prefix = "${local.name}-media-"
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3 (AWS-managed keys): a customer-managed KMS key costs money and adds nothing for this bucket yet.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# --- Configuration read by deploy/ec2/remote-deploy.sh ---------------------------------------------------
# The app settings (secrets) live in "${local.ssm_path}/app-env", written by hand: see infra/aws/README.md.

resource "aws_ssm_parameter" "api_host" {
  name  = "${local.ssm_path}/api-host"
  type  = "String"
  value = local.api_host
}

# --- Instance permissions --------------------------------------------------------------------------------

resource "aws_iam_role" "api" {
  name = "${local.name}-instance"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "api" {
  name = "app"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PullImages"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.backend.arn
      },
      { Sid = "EcrLogin", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Sid      = "ReadConfig"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = "${local.ssm_arn}/*"
      },
      {
        Sid      = "MediaBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.media.arn
      },
      {
        Sid      = "MediaObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.media.arn}/*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "api" {
  name = "${local.name}-instance"
  role = aws_iam_role.api.name
}

# --- GitHub Actions deploy role (OIDC, no stored AWS keys) -----------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

resource "aws_iam_role" "deploy" {
  name = "${local.name}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_host}:aud" = "sts.amazonaws.com"
          # Only jobs running in this repository's GitHub environment of the same name.
          "${local.oidc_host}:sub" = "repo:${var.github_repository}:environment:${var.environment}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EcrLogin", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Sid    = "PushImages"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        ]
        Resource = aws_ecr_repository.backend.arn
      },
      {
        Sid    = "RunDeployCommand"
        Effect = "Allow"
        Action = "ssm:SendCommand"
        Resource = [
          aws_instance.api.arn,
          "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
        ]
      },
      {
        Sid      = "ReadDeployCommand"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}
