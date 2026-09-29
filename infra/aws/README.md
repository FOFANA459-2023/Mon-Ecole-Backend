# AWS — free-tier production (single EC2 instance)

One Graviton instance (`t4g.small`, Amazon Linux 2023) runs the whole API with Docker Compose
([deploy/ec2](../../deploy/ec2)): Caddy (HTTPS, automatic certificates) → gunicorn, Celery worker and beat,
Valkey. PostgreSQL is **Supabase Free**, uploaded files go to a private **S3** bucket, images to **ECR**.
There is no SSH: GitHub Actions deploys through SSM Run Command, and you get a shell through Session Manager.

Terraform creates: security group (80/443 only), elastic IP, instance + role, ECR repository, S3 bucket, the
`/mon-ecole/<env>/api-host` parameter and the GitHub OIDC deploy role. It never sees the app's secrets.

**Cost** (Paris, paid from the AWS Free plan credits): instance ~$13, public IPv4 ~$3.60, 20 GB disk ~$1.90,
S3/ECR cents — about **$19/month**. `instance_type = "t4g.micro"` (1 GB, tight) saves ~$6. The Free plan ends
after 6 months or when the credits run out; upgrade the account to the paid plan before then or AWS closes it.

## First setup

1. **Supabase** — create a Free project in *West EU (Paris)*. Copy the **Transaction pooler** connection
   string (Connect → port 6543) and add `?sslmode=require`.

2. **AWS CLI** — in the AWS console create an IAM user with `AdministratorAccess` and an access key, then:
   ```
   aws configure        # region: eu-west-3
   ```

3. **Terraform**
   ```
   cd infra/aws
   terraform init
   terraform apply
   ```
   Keep `terraform.tfstate` (git-ignored) safe: it is the only record of what was created.

4. **App settings** — write `app.env` (outside the repository) and store it as a SecureString:
   ```
   DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(50))">
   DATABASE_URL=postgres://postgres.<ref>:<password>@aws-0-eu-west-3.pooler.supabase.com:6543/postgres?sslmode=require
   AWS_STORAGE_BUCKET_NAME=<terraform output media_bucket>
   AWS_S3_REGION_NAME=eu-west-3
   FRONTEND_URL=https://mon-ecole.<account>.workers.dev
   CORS_ALLOWED_ORIGINS=https://mon-ecole.<account>.workers.dev
   SENTRY_ENVIRONMENT=production
   # Optional: EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_PUBLIC_KEY, EMAILJS_PRIVATE_KEY, SENTRY_DSN
   ```
   ```
   aws ssm put-parameter --name /mon-ecole/production/app-env --type SecureString --value file://app.env --overwrite
   ```
   Change a setting later: edit the file, run the same command, then redeploy.

5. **GitHub** (Mon-Ecole-Backend → Settings):
   - *Environments* → create `production` → add the four variables from `terraform output github_environment_variables`;
   - *Secrets and variables → Actions → Variables* → `DEPLOY_PRODUCTION` = `true`.

   Every push to `main` now deploys. To deploy without a push: *Actions → Deploy → Run workflow*, environment
   `production`, image `ghcr.io/fofana459-2023/mon-ecole-backend:main`.

6. **Frontend** — in the Cloudflare Worker (*Settings → Build → Variables*) set
   `VITE_API_URL` = `<terraform output api_url>/api/v1`, then redeploy it.

7. **First school** — EC2 console → the instance → *Connect → Session Manager*, then:
   ```
   sudo -i
   cd /opt/mon-ecole
   docker compose exec api python manage.py create_school --name "École Horizon" --code horizon --admin-email you@example.org
   ```
   Without EmailJS the invitation email (with its link) is printed in `docker compose logs worker api`.

## Day to day

| Task | How |
|---|---|
| Logs | Session Manager → `cd /opt/mon-ecole && docker compose logs -f api` (also `worker`, `beat`, `caddy`) |
| Roll back | *Actions → Deploy → Run workflow* with an older image tag (`sha-1a2b3c4`) |
| Django shell | `docker compose exec api python manage.py shell` |
| Use your own domain | DNS `A` record `api.example.org` → `terraform output public_ip`; `terraform apply -var api_domain=api.example.org`; redeploy; update `VITE_API_URL`, `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` |

Until the app and the API share a domain (e.g. `app.example.org` + `api.example.org`), the browser does not
send the refresh cookie from the `workers.dev` app to the API: signing in works, but a page reload signs out.
