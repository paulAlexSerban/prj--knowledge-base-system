# Creates queues and the raw-snapshot bucket on floci. This is not a cloud
# account: there is no VPC, database, or compute. Scheduler schedules are
# intentionally absent. EventBridge Scheduler cannot fire faster than once
# a minute, and a schedule also needs an IAM role we are not inventing here.
# P0 polling lives in the connector worker.

variable "aws_endpoint" {
  type    = string
  default = "http://127.0.0.1:4566"
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    s3  = var.aws_endpoint
    sqs = var.aws_endpoint
  }
}

locals {
  tiers = ["p0", "p1", "p2"]
}

resource "aws_sqs_queue" "dlq" {
  for_each                    = toset(local.tiers)
  name                        = "kbs-${each.key}-dlq.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
}

resource "aws_sqs_queue" "tier" {
  for_each                    = toset(local.tiers)
  name                        = "kbs-${each.key}.fifo"
  fifo_queue                  = true
  content_based_deduplication = true
  # Longer than one embed call, so a crashed worker's message becomes visible again.
  visibility_timeout_seconds  = 60
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 5
  })
}

resource "aws_s3_bucket" "raw_snapshots" {
  bucket = "kbs-raw-snapshots"
}

resource "aws_s3_bucket_versioning" "raw_snapshots" {
  bucket = aws_s3_bucket.raw_snapshots.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "raw_snapshots" {
  bucket                  = aws_s3_bucket.raw_snapshots.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
