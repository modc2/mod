# Phone-server AWS IAM least-privilege assessment

Assessment date: 2026-07-12
Account: `302154194530`
Region: `us-east-1`

This document began as a design review. The live hardening described in `README.md` was subsequently applied and verified. The remaining deliberate gap is removal of `AdministratorAccess` from `jade-deploy`, which is blocked until an independent root/MFA recovery login is verified.

## Executive assessment

`jade-deploy` should not retain `AdministratorAccess`. A compromised long-lived deployment credential can currently modify identities, disable cost guardrails, exfiltrate data, create arbitrary infrastructure, and establish persistence anywhere in the account.

Replace it with an assume-role-only principal and three distinct privilege planes:

1. **`JcodePhoneOperator`** for normal deployments and maintenance of the existing stack.
2. **`JcodePhoneProvisioner`** for infrequent rebuilds, MFA-gated, time-limited, restricted to `us-east-1`, `jcode-phone-*` resources, and bounded runtime roles.
3. **A separate emergency administrator path** protected by MFA and never used by automation. This is required to avoid lockout while removing the existing administrator attachment.

Also replace the instance's `AmazonBedrockFullAccess` with inference-only permissions. The code uses model catalog discovery and `ConverseStream`; it does not need Bedrock administration.

## Evidence and stated live resources

Repository evidence:

- `README.md` states account `302154194530`, region `us-east-1`, instance `i-08214cf66cd3f80c7`, Elastic IP `54.196.207.97`, and API ID `8c3wp4cbag`.
- `wake-lambda.py` calls `ec2:DescribeInstances` and `ec2:StartInstances` for that instance.
- `breaker-lambda.py` calls `ec2:DescribeInstances`, `ec2:StopInstances`, and `sns:Publish` to `arn:aws:sns:us-east-1:302154194530:jcode-guard-warn`.
- The Bedrock provider calls `ListFoundationModels`, `ListInferenceProfiles`, and the streaming Converse API. Converse streaming is authorized by `bedrock:InvokeModelWithResponseStream`.
- The rebuild instructions require EC2, EIP, security group, IAM instance profile, Lambda, API Gateway v2, SNS, CloudWatch alarms, and Budgets administration.
- TestFlight automation is App Store Connect only and requires no AWS permission.

The SSM-based wake implementation is now deployed. The Lambda generates pair codes through SSM Run Command, and the legacy public `:7644` path is disabled.

Stated named resources:

| Type | Resource |
|---|---|
| EC2 instance | `arn:aws:ec2:us-east-1:302154194530:instance/i-08214cf66cd3f80c7` |
| Elastic IP | `54.196.207.97`; allocation ID must be inventoried |
| Lambda | `jcode-phone-wake` |
| Lambda | `jcode-guard-breaker` |
| API Gateway v2 | `8c3wp4cbag` |
| SNS | `jcode-guard-stop` and `jcode-guard-warn` |
| CloudWatch alarms | `jcode-bedrock-tokens-warn`, `jcode-bedrock-tokens-stop`; the ineffective billing alarms were replaced by the working Budget/SNS breaker path |
| Budget | `jcode-dev-monthly-cost` |
| Bedrock model route | `us.anthropic.claude-opus-4-6-v1` |

### Live-state verification

The account was subsequently inventoried through the `jcode-bedrock` profile. Runtime role names, Lambda roles, the EC2 instance and security group, the Elastic IP, Budget subscribers, CloudWatch alarms, log retention, API Gateway stage, S3/DynamoDB resources, and access-key metadata were verified directly. The wake Lambda now uses SSM pairing, all public EC2 ingress is closed, the root EBS volume is encrypted, CloudTrail and Access Analyzer are enabled, and the old deployment key is inactive.

## Target identity design

### Human access

Preferred end state:

- Use IAM Identity Center or another federated identity for the human operator.
- Require MFA for both operator and provisioner role assumption.
- Do not create long-lived keys for human access.
- Keep `jade-deploy` only during migration, then delete it after the observation period.

Transitional end state if an IAM user must remain:

- `jade-deploy` has no console password and no service permissions.
- Its only permission is `sts:AssumeRole` for the two deployment roles.
- It cannot modify itself, policies, access keys, MFA devices, or role trust policies.

Assume-only policy for `jade-deploy`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumePhoneServerRolesOnly",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::302154194530:role/jcode-phone/JcodePhoneOperator",
        "arn:aws:iam::302154194530:role/jcode-phone/JcodePhoneProvisioner"
      ]
    }
  ]
}
```

Require MFA in both role trust policies for a human IAM principal:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::302154194530:user/jade-deploy"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "Bool": { "aws:MultiFactorAuthPresent": "true" },
        "NumericLessThan": { "aws:MultiFactorAuthAge": "3600" }
      }
    }
  ]
}
```

Set `JcodePhoneProvisioner` maximum session duration to one hour. Do not allow either role to change its own trust or permissions.

For unattended CI, use a separate OIDC-federated role restricted to the exact repository, branch/environment, and workflow. Do not weaken the human role's MFA trust for CI.

## Runtime policies

Use separate execution roles for the instance and each Lambda. Do not reuse the deploy role at runtime.

### EC2 instance: inference only

Replace `AmazonBedrockFullAccess` with the following customer-managed policy. `List*` actions require `Resource: "*"`. The inference resources deliberately cover only the configured Claude Opus 4.6 cross-region profile and its backing foundation model. Cross-region inference can route outside `us-east-1`, so a single-region foundation-model ARN may fail.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DiscoverBedrockModels",
      "Effect": "Allow",
      "Action": [
        "bedrock:ListFoundationModels",
        "bedrock:ListInferenceProfiles"
      ],
      "Resource": "*"
    },
    {
      "Sid": "InvokeConfiguredClaudeProfile",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1:302154194530:inference-profile/us.anthropic.claude-opus-4-6-v1",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-6-v1:0"
      ]
    }
  ]
}
```

Verify the exact inference-profile ARN and backing model IDs with `bedrock list-inference-profiles` before application. If the deployed profile is AWS-owned or the backing model has a different version suffix, substitute the returned ARNs rather than widening the model-name pattern. The wildcard region is intentional because a US cross-region inference profile can route to multiple US regions. Remove `bedrock:InvokeModel` after testing if all production paths exclusively use `ConverseStream`; retaining it is a small compatibility allowance, not administrative access. The provider's optional STS identity validation does not require an added service permission for normal operation.

### Wake Lambda

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadTargetInstanceState",
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    },
    {
      "Sid": "StartTargetInstanceOnly",
      "Effect": "Allow",
      "Action": "ec2:StartInstances",
      "Resource": "arn:aws:ec2:us-east-1:302154194530:instance/i-08214cf66cd3f80c7"
    },
    {
      "Sid": "WriteOwnLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:302154194530:log-group:/aws/lambda/jcode-phone-wake:*"
    }
  ]
}
```

Create the log group during provisioning with retention configured, rather than granting runtime `logs:CreateLogGroup` on `*`.

If the SSM-based wake implementation is deployed, add:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadManagedInstanceStatus",
      "Effect": "Allow",
      "Action": "ssm:DescribeInstanceInformation",
      "Resource": "*"
    },
    {
      "Sid": "RunPairCommandOnTargetOnly",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:us-east-1:302154194530:instance/i-08214cf66cd3f80c7",
        "arn:aws:ssm:us-east-1::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "ReadPairCommandResult",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    }
  ]
}
```

That variant also requires an SSM-managed-instance policy on the EC2 role. Keep it separate from the Bedrock policy, validate the exact Systems Manager agent calls in CloudTrail, and narrow it from `AmazonSSMManagedInstanceCore` where operationally practical. Treat `ssm:SendCommand` as privileged remote code execution and keep it scoped to the one instance and AWS-managed document.

### Breaker Lambda

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadTargetInstanceState",
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    },
    {
      "Sid": "StopTargetInstanceOnly",
      "Effect": "Allow",
      "Action": "ec2:StopInstances",
      "Resource": "arn:aws:ec2:us-east-1:302154194530:instance/i-08214cf66cd3f80c7"
    },
    {
      "Sid": "PublishGuardNotification",
      "Effect": "Allow",
      "Action": "sns:Publish",
      "Resource": "arn:aws:sns:us-east-1:302154194530:jcode-guard-warn"
    },
    {
      "Sid": "WriteOwnLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:302154194530:log-group:/aws/lambda/jcode-guard-breaker:*"
    }
  ]
}
```

## Daily operator policy

This role updates and diagnoses existing resources but cannot create identities, create arbitrary compute, terminate the instance, release the EIP, delete guardrails, or alter role trust.

Replace the `<...>` values after read-only inventory. The API Gateway resource form is intentionally the API Gateway management ARN, which does not include the account ID.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DescribePhoneServerInfrastructure",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeAddresses",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeVolumes",
        "cloudwatch:DescribeAlarms",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ReadExistingFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:GetPolicy"
      ],
      "Resource": [
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-phone-wake",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-phone-wake:*",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-guard-breaker",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-guard-breaker:*"
      ]
    },
    {
      "Sid": "OperateExistingInstance",
      "Effect": "Allow",
      "Action": [
        "ec2:StartInstances",
        "ec2:StopInstances",
        "ec2:RebootInstances",
        "ec2:ModifyInstanceAttribute"
      ],
      "Resource": "arn:aws:ec2:us-east-1:302154194530:instance/i-08214cf66cd3f80c7"
    },
    {
      "Sid": "DeployExistingFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:PublishVersion",
        "lambda:CreateAlias",
        "lambda:UpdateAlias",
        "lambda:DeleteAlias",
        "lambda:InvokeFunction"
      ],
      "Resource": [
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-phone-wake",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-phone-wake:*",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-guard-breaker",
        "arn:aws:lambda:us-east-1:302154194530:function:jcode-guard-breaker:*"
      ]
    },
    {
      "Sid": "ReadAndPatchExistingHttpApiRoot",
      "Effect": "Allow",
      "Action": [
        "apigateway:GET",
        "apigateway:PATCH"
      ],
      "Resource": "arn:aws:apigateway:us-east-1::/apis/8c3wp4cbag"
    },
    {
      "Sid": "MaintainExistingHttpApiChildren",
      "Effect": "Allow",
      "Action": [
        "apigateway:GET",
        "apigateway:POST",
        "apigateway:PUT",
        "apigateway:PATCH",
        "apigateway:DELETE"
      ],
      "Resource": "arn:aws:apigateway:us-east-1::/apis/8c3wp4cbag/*"
    },
    {
      "Sid": "PassInstanceRoleToEc2Only",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::302154194530:role/jcode-phone/runtime/JcodePhoneInstance",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "ec2.amazonaws.com"
        }
      }
    },
    {
      "Sid": "PassLambdaRolesToLambdaOnly",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::302154194530:role/jcode-phone/runtime/JcodePhoneWakeLambda",
        "arn:aws:iam::302154194530:role/jcode-phone/runtime/JcodePhoneBreakerLambda"
      ],
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "lambda.amazonaws.com"
        }
      }
    },
    {
      "Sid": "ReadPhoneLogs",
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogStreams",
        "logs:GetLogEvents",
        "logs:FilterLogEvents"
      ],
      "Resource": [
        "arn:aws:logs:us-east-1:302154194530:log-group:/aws/lambda/jcode-phone-wake:*",
        "arn:aws:logs:us-east-1:302154194530:log-group:/aws/lambda/jcode-guard-breaker:*"
      ]
    },
    {
      "Sid": "MaintainGuardTopics",
      "Effect": "Allow",
      "Action": [
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:Publish"
      ],
      "Resource": [
        "arn:aws:sns:us-east-1:302154194530:jcode-guard-stop",
        "arn:aws:sns:us-east-1:302154194530:jcode-guard-warn"
      ]
    },
    {
      "Sid": "ReadNamedBudget",
      "Effect": "Allow",
      "Action": "budgets:ViewBudget",
      "Resource": "arn:aws:budgets::302154194530:budget/jcode-dev-monthly-cost"
    }
  ]
}
```

Recommended tightening:

- Remove `ec2:ModifyInstanceAttribute` if routine maintenance never changes shutdown behavior, instance type, source/destination check, or attached profile.
- Keep `lambda:AddPermission`, `lambda:RemovePermission`, `sns:Subscribe`, alarm mutation, and budget mutation in the MFA-gated provisioner path. Those actions can expose invocations, exfiltrate notifications, or disable cost controls.
- API Gateway `DELETE` is allowed only below `/apis/8c3wp4cbag/*`; the daily role cannot delete the API root.
- Do not grant `cloudwatch:DeleteAlarms`, `budgets:DeleteBudget`, `sns:DeleteTopic`, `ec2:TerminateInstances`, `ec2:ReleaseAddress`, or IAM write actions to the daily operator.

## Rebuild/provisioner role

A rebuild inherently needs create/delete privileges on several services. Keep those permissions out of the daily role. The safest maintainable implementation is to put the stack in CloudFormation, CDK, or Terraform and let the human invoke only a named stack deployment role.

Recommended model:

- Human `JcodePhoneProvisioner`: CloudFormation stack operations on `jcode-phone-server*`, read-only diagnostics, and `iam:PassRole` only for `JcodePhoneCloudFormationExecution` with `iam:PassedToService = cloudformation.amazonaws.com`.
- `JcodePhoneCloudFormationExecution`: service permissions below, usable only by CloudFormation.
- Every created resource is tagged `Project=jcode-phone-server` and `ManagedBy=cloudformation`.
- Every created runtime role is under path `/jcode-phone/runtime/` and must carry the `JcodePhoneRuntimeBoundary` permissions boundary.

Human provisioner policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageNamedPhoneStack",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary",
        "cloudformation:ListStackResources",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet",
        "cloudformation:ValidateTemplate"
      ],
      "Resource": [
        "arn:aws:cloudformation:us-east-1:302154194530:stack/jcode-phone-server*/*",
        "arn:aws:cloudformation:us-east-1:302154194530:changeSet/jcode-phone-server*/*"
      ]
    },
    {
      "Sid": "ValidateNewTemplate",
      "Effect": "Allow",
      "Action": "cloudformation:ValidateTemplate",
      "Resource": "*"
    },
    {
      "Sid": "PassPhoneCloudFormationExecutionRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::302154194530:role/jcode-phone/JcodePhoneCloudFormationExecution",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "cloudformation.amazonaws.com"
        }
      }
    }
  ]
}
```

The CloudFormation execution role should permit only this service/action envelope:

| Service | Required rebuild operations | Scope/guardrail |
|---|---|---|
| EC2 | Run/terminate the one server; create/tag volume and ENI; create/manage one SG; allocate/associate/release one EIP; modify shutdown behavior; describe AMIs/subnets/VPCs | `us-east-1`; request/resource tag `Project=jcode-phone-server`; approved instance types only; IMDSv2 required; approved VPC/subnet |
| IAM | Create/update/delete the three runtime roles and one instance profile; put/delete inline policies; pass roles | Path `/jcode-phone/runtime/` only; require boundary ARN on `CreateRole`; never allow changing deploy/provisioner/emergency roles |
| Lambda | Create/update/delete the two named functions; versions/aliases; permissions | Function ARN prefix `jcode-phone-*` and `jcode-guard-breaker*`; project tag |
| API Gateway v2 | Create/update/delete one HTTP API, integration, route, and stage | Project tag and stack ownership |
| SNS | Create/manage/delete `jcode-guard-stop` and `jcode-guard-warn`; subscriptions | Exact topic-name ARNs |
| CloudWatch | Create/update/delete the four named alarms | Exact alarm-name ARNs |
| Logs | Create/configure/delete the two Lambda log groups | Exact `/aws/lambda/...` ARNs; retention required |
| Budgets | Create/update/delete `jcode-dev-monthly-cost` | Exact budget ARN |

The execution role, not the daily operator, also owns `lambda:AddPermission`/`RemovePermission`, `sns:Subscribe`/`Unsubscribe`, CloudWatch alarm mutation, and budget mutation. This preserves integration and guardrail maintenance without making those high-impact actions part of everyday credentials.

Do not give the CloudFormation execution role general `iam:*`, `organizations:*`, `account:*`, `sts:AssumeRole`, `kms:*`, unrestricted `s3:*`, unrestricted `secretsmanager:*`, or permission to modify the provisioner, operator, emergency role, its own role, or its own boundary.

A permissions boundary is not a grant. Use it as a maximum for runtime roles and combine it with their narrow inline policies. The boundary should allow only:

- The instance's Bedrock discovery and configured-model inference permissions.
- The wake Lambda's start/describe and own-log permissions.
- The breaker Lambda's stop/describe, warning publish, and own-log permissions.

Because these permissions differ, the boundary can be their union; each role's inline policy must remain the narrower subset. Explicitly deny IAM, STS role assumption, Organizations, account administration, and access-key operations in the boundary as defense in depth.

## Read-only inventory required before migration

Run these with the existing administrator session after reauthentication. They make no changes:

```bash
aws sts get-caller-identity
aws iam list-attached-user-policies --user-name jade-deploy
aws iam list-user-policies --user-name jade-deploy
aws iam list-access-keys --user-name jade-deploy
aws iam get-account-authorization-details > phone-server-iam-before.json

aws ec2 describe-instances --instance-ids i-08214cf66cd3f80c7 --region us-east-1
aws ec2 describe-addresses --public-ips 54.196.207.97 --region us-east-1
aws lambda get-function --function-name jcode-phone-wake --region us-east-1
aws lambda get-function --function-name jcode-guard-breaker --region us-east-1
aws apigatewayv2 get-api --api-id 8c3wp4cbag --region us-east-1
aws sns list-topics --region us-east-1
aws cloudwatch describe-alarms --alarm-name-prefix jcode- --region us-east-1
aws budgets describe-budget --account-id 302154194530 --budget-name jcode-dev-monthly-cost
aws bedrock list-inference-profiles --type-equals SYSTEM_DEFINED --region us-east-1
```

Also inventory CloudTrail usage for at least 30 days. Add an action only when it corresponds to a known deploy/maintenance operation. Access Analyzer policy generation from CloudTrail is useful as a second opinion, but should not replace review because rarely used recovery operations may be absent.

## Lockout-safe migration and credential rotation

1. **Prepare recovery first.** Verify the account root email, root MFA, and recovery contacts. Create or verify a separate MFA-protected emergency administrator path. Prefer IAM Identity Center. It must be independent of `jade-deploy`, have no access key, and be tested before touching `AdministratorAccess`.
2. **Capture state.** Export IAM authorization details, user policy attachments, role trust policies, access-key metadata, resource ARNs, and tags. Record the exact `AdministratorAccess` attachment being replaced.
3. **Create runtime policies and roles.** Create the narrow instance, wake, and breaker roles. Do not switch workloads yet. Validate their documents with IAM Access Analyzer or `aws accessanalyzer validate-policy`.
4. **Create operator and provisioner roles.** Add MFA-gated trust. Ensure neither role can modify itself, the emergency path, permission boundaries, or arbitrary identities.
5. **Grant assume-only access while admin remains attached.** Attach the small `sts:AssumeRole` policy to `jade-deploy`, but leave `AdministratorAccess` temporarily attached.
6. **Test role entry.** From a distinct profile/session, assume each role and confirm `aws sts get-caller-identity` reports the role ARN. Confirm operator reads for EC2, Lambda, API Gateway, logs, SNS, alarms, and budget.
7. **Simulate every required API call.** Use `iam:SimulatePrincipalPolicy` or the policy simulator for the action/resource matrix in this document. Explicitly test denied controls such as creating users, attaching `AdministratorAccess`, terminating arbitrary instances, and deleting guardrails.
8. **Canary mutation paths without touching production.** Through the provisioner, deploy and remove a tiny tagged canary stack using the same resource classes where practical. For the operator, update a disposable Lambda alias or canary function rather than invoking `jcode-guard-breaker`, which stops production. Do not use the breaker invocation as an IAM test.
9. **Switch runtime roles one at a time.** First update Lambda execution roles and verify logs plus harmless wake status checks. Then replace the EC2 instance profile and run a real Bedrock streaming request. Keep the previous roles intact but unattached until verification completes.
10. **Rotate the credential transport.** Preferred: switch the workstation to IAM Identity Center and role profiles. Transitional IAM-user option: create a second `jade-deploy` access key, configure it under a new profile, and verify role assumption. Never overwrite the only working profile first. An IAM user can have at most two keys.
11. **Detach `AdministratorAccess` only after independent recovery succeeds.** Confirm the emergency administrator path in a separate browser/profile immediately before detachment. Then detach `AdministratorAccess` from `jade-deploy`. Do not delete the user, keys, old runtime roles, or old policies in the same change window.
12. **Run post-detachment checks.** Re-assume operator and provisioner, rerun all read checks and safe deployment checks, verify the phone-server health endpoint, verify wake behavior during an agreed maintenance window, verify pairing, and verify one Bedrock streaming completion.
13. **Disable the old key, do not delete it yet.** After the new access path has worked for at least 24 to 72 hours, mark the old key inactive. Monitor CloudTrail for attempts using its access-key ID and for `AccessDenied` on expected workflows.
14. **Delete after observation.** After another 7 to 14 days without needed rollback, delete the inactive key, remove obsolete policies/roles, and, if federation is stable, delete `jade-deploy` entirely.
15. **Review quarterly.** Use access-key last-used data, CloudTrail, Access Analyzer, credential reports, and alarm/budget tests. Remove unused actions. Test the emergency path without using it for routine work.

### Rollback rule

If a required operation fails after administrator removal, stop and use the independent emergency administrator path to correct the narrow role. Do not reattach `AdministratorAccess` to the everyday user as the default fix. Never rely on an existing STS session as the only rollback mechanism because policy changes and session expiry can invalidate that assumption.

## Additional security findings adjacent to IAM

These are not required for the IAM replacement, but they materially affect the deployment:

- The wake secret is embedded in Lambda source and placed in a query string. Query tokens can appear in browser history, logs, analytics, screenshots, and referrers. Store it in Secrets Manager or SSM Parameter Store, compare a header or signed short-lived request, and grant only the wake Lambda read access to that one secret.
- Port `7644` is publicly exposed and the Lambda calls it over plain HTTP. Prefer a private VPC path, SSM-mediated pairing, or tailnet-only access. If keeping it public, restrict the security group source and add TLS.
- The pair service runs as root and invokes `sudo -u ec2-user`; harden the systemd unit with a dedicated user, filesystem protections, `NoNewPrivileges`, and a narrowly scoped sudo rule.
- The instance role's current `AmazonBedrockFullAccess` is broader than necessary even if `jade-deploy` is fixed.
- Add CloudTrail alerts for `AttachUserPolicy`, `PutUserPolicy`, `CreateAccessKey`, `UpdateAssumeRolePolicy`, `PassRole`, and changes to the emergency/deployment roles.

## Acceptance criteria

The migration is complete when:

- `jade-deploy` has no `AdministratorAccess` and no direct AWS service permissions beyond role assumption.
- Daily deployment and maintenance succeed through `JcodePhoneOperator`.
- A full tagged rebuild can be performed through the MFA-gated provisioner/CloudFormation path.
- Runtime roles contain only the API calls documented above.
- An independent emergency administrator path is tested.
- The old access key is disabled, observed, then deleted.
- CloudTrail shows no unexpected `AccessDenied` for required operations and no use of the old key during the observation period.
