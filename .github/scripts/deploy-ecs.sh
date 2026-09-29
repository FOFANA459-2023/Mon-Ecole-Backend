#!/usr/bin/env bash
# Roll an ECS environment onto a new image (used by .github/workflows/deploy.yml):
#   1. register a new task-definition revision per service: same settings, new image;
#   2. run the database migrations once, as a one-off Fargate task, and stop here if they fail;
#   3. point every service at its new revision and wait until all of them are stable.
# The cluster, services and task definitions already exist (created by Terraform).
set -euo pipefail

image="${1:?usage: deploy-ecs.sh <image>}"
: "${ECR_REPOSITORY:?}" "${ECS_CLUSTER:?}" "${ECS_SERVICES:?}" "${ECS_SUBNETS:?}" "${ECS_SECURITY_GROUPS:?}"
assign_public_ip="${ECS_ASSIGN_PUBLIC_IP:-ENABLED}"

read -r -a services <<< "$ECS_SERVICES"
declare -A revisions

# Copy a task definition, swapping the image of the container(s) built from this repository.
register_with_image() {
  aws ecs describe-task-definition --task-definition "$1" --query taskDefinition --output json \
    | jq --arg image "$image" --arg repo "$ECR_REPOSITORY" '
        if any(.containerDefinitions[]; .image | startswith($repo)) then . else
          error("no container uses an image from \($repo)") end
        | .containerDefinitions |= map(if (.image | startswith($repo)) then .image = $image else . end)
        | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
              .registeredAt, .registeredBy, .deregisteredAt)' \
    > task-definition.json
  aws ecs register-task-definition --cli-input-json file://task-definition.json \
    --query taskDefinition.taskDefinitionArn --output text
}

echo "::group::Register task definitions"
for service in "${services[@]}"; do
  current=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" \
    --query 'services[0].taskDefinition' --output text)
  if [[ -z "$current" || "$current" == "None" ]]; then
    echo "::error::Service '$service' was not found in cluster '$ECS_CLUSTER'."
    exit 1
  fi
  revisions[$service]=$(register_with_image "$current")
  echo "$service: $current -> ${revisions[$service]}"
done
rm -f task-definition.json
echo "::endgroup::"

echo "::group::Run migrations"
api_revision="${revisions[${services[0]}]}"
container=$(aws ecs describe-task-definition --task-definition "$api_revision" \
  --query "taskDefinition.containerDefinitions[?image=='$image'].name | [0]" --output text)
overrides=$(jq -cn --arg name "$container" \
  '{containerOverrides: [{name: $name, command: ["python", "manage.py", "migrate", "--noinput"]}]}')
task=$(aws ecs run-task --cluster "$ECS_CLUSTER" --launch-type FARGATE --started-by github-deploy \
  --task-definition "$api_revision" --overrides "$overrides" \
  --network-configuration "awsvpcConfiguration={subnets=[$ECS_SUBNETS],securityGroups=[$ECS_SECURITY_GROUPS],assignPublicIp=$assign_public_ip}" \
  --query 'tasks[0].taskArn' --output text)
if [[ -z "$task" || "$task" == "None" ]]; then
  echo "::error::The migration task could not be started."
  exit 1
fi
echo "Migration task: $task"
aws ecs wait tasks-stopped --cluster "$ECS_CLUSTER" --tasks "$task"
exit_code=$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$task" \
  --query "tasks[0].containers[?name=='$container'].exitCode | [0]" --output text)
if [[ "$exit_code" != "0" ]]; then
  echo "::error::Migrations failed (exit code $exit_code). The services still run the previous version."
  exit 1
fi
echo "::endgroup::"

echo "::group::Update services"
for service in "${services[@]}"; do
  aws ecs update-service --cluster "$ECS_CLUSTER" --service "$service" \
    --task-definition "${revisions[$service]}" --query 'service.serviceName' --output text
done
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services "${services[@]}"
echo "::endgroup::"
echo "Deployed $image to $ECS_CLUSTER (${services[*]})."
