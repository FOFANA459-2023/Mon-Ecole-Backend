#!/usr/bin/env bash
# Deploy one image to the single-instance environment (infra/aws), used by .github/workflows/deploy.yml:
# copy deploy/ec2 to the server and run remote-deploy.sh there through SSM Run Command (no SSH).
set -euo pipefail

image="${1:?usage: deploy-ec2.sh <image>}"
: "${EC2_INSTANCE_ID:?}" "${DEPLOY_ENVIRONMENT:?}" "${AWS_REGION:?}"

files=(compose.yaml Caddyfile remote-deploy.sh)
script="set -euo pipefail
mkdir -p /opt/mon-ecole && cd /opt/mon-ecole"
for file in "${files[@]}"; do
  script+="
echo '$(base64 -w0 "deploy/ec2/$file")' | base64 -d > '$file'"
done
script+="
bash remote-deploy.sh '$image' '$DEPLOY_ENVIRONMENT' '$AWS_REGION'"

parameters=$(jq -cn --arg script "$script" '{commands: [$script], executionTimeout: ["1200"]}')
command_id=$(aws ssm send-command --instance-ids "$EC2_INSTANCE_ID" --document-name AWS-RunShellScript \
  --comment "Deploy ${image##*/}" --parameters "$parameters" --query Command.CommandId --output text)
echo "SSM command: $command_id"

status=Pending
while [[ "$status" =~ ^(Pending|InProgress|Delayed)$ ]]; do
  sleep 10
  status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$EC2_INSTANCE_ID" \
    --query Status --output text 2>/dev/null || echo Pending)
done

echo "::group::Server output"
aws ssm get-command-invocation --command-id "$command_id" --instance-id "$EC2_INSTANCE_ID" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
echo "::endgroup::"

if [[ "$status" != "Success" ]]; then
  echo "::error::Deploy finished with status $status. The previous version is still running unless the restart step was reached."
  exit 1
fi
