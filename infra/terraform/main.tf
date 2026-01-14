// infra/k8s/terraform/main.tf
terraform {
  required_version = ">= 1.2.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.11.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.7.0"
    }
  }

  # Для продакшн можно настроить backend (s3/gcs/azurerm) здесь.
  # backend "s3" {
  #   bucket = "my-terraform-state"
  #   key    = "infra/k8s/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

#############
# Variables #
#############

variable "kubeconfig" {
  description = "Путь к kubeconfig для подключения к кластеру (или null, если будет использован in-cluster provider)."
  type        = string
  default     = ""
}

variable "namespace" {
  description = "Kubernetes namespace для приложения"
  type        = string
  default     = "finassist"
}

variable "labels" {
  description = "Дополнительные метки для namespace и общих ресурсов"
  type        = map(string)
  default     = {
    environment = "dev"
    managed_by  = "terraform"
  }
}

################
# Providers    #
################

provider "kubernetes" {
  # Для локальной отладки: указывайте путь к kubeconfig через переменную.
  # В CI/CD можно передавать kubeconfig через environment/secret.
  config_path = var.kubeconfig != "" ? var.kubeconfig : null
  # Альтернативно можно задать host/token/cert_data при необходимости.
}

provider "helm" {
  kubernetes {
    config_path = var.kubeconfig != "" ? var.kubeconfig : null
  }
  # Доп: можно задать версии и настройки (debug, etc.)
}

#########################
# Basic Kubernetes items#
#########################

# Namespace для приложения
resource "kubernetes_namespace" "finassist" {
  metadata {
    name = var.namespace
    labels = merge(
      {
        app = "finassist"
      },
      var.labels
    )
  }
}

# Пример: optionals - service account, role и binding (placeholders)
resource "kubernetes_service_account" "tf_deployer" {
  metadata {
    name      = "tf-deployer"
    namespace = kubernetes_namespace.finassist.metadata[0].name
    labels    = var.labels
  }
}

# Оставлено минимально: Role/RoleBinding добавляйте в зависимости от требований
# resource "kubernetes_role" "example" { ... }
# resource "kubernetes_role_binding" "example_binding" { ... }

#########################
# Example Helm releases #
#########################

# Ниже - пример как подключать helm-релизы (закомментировано — включайте по потребности)
# release nginx-ingress (пример для тестового окружения)
# resource "helm_release" "nginx_ingress" {
#   name       = "nginx-ingress"
#   namespace  = kubernetes_namespace.finassist.metadata[0].name
#   repository = "https://kubernetes.github.io/ingress-nginx"
#   chart      = "ingress-nginx"
#   version    = "4.4.0"
#   values = [
#     yamlencode({
#       controller = {
#         service = {
#           type = "ClusterIP"
#         }
#       }
#     })
#   ]
# }

#########################
# Place for modules     #
#########################

# Здесь вы можете подключить модули (GKE/EKS/AKS, ingress, cert-manager, monitoring, etc.)
# Пример:
# module "gke" {
#   source  = "git::ssh://git@.../terraform-modules/gke.git//modules/cluster"
#   project = var.gcp_project
#   region  = var.region
#   ...
# }

################
# Outputs      #
################

output "namespace" {
  description = "Kubernetes namespace created for finassist"
  value       = kubernetes_namespace.finassist.metadata[0].name
}

# Выходы для удобства (например kubeconfig, helm release status) можно добавить по мере необходимости.
# output "nginx_ingress_status" {
#   value = helm_release.nginx_ingress.status
#   description = "Status of nginx ingress helm release (if enabled)."
#   depends_on = [helm_release.nginx_ingress]
# }

######################
# Notes / next steps #
######################

# 1) Для production определите terraform backend (s3/gcs/azurerm) в блоке terraform.backend.
# 2) Добавьте модули для создания кластера (если вы хотите управлять кластером из Terraform),
#    либо используйте провайдеры cloud (google/aws/azurerm) для управления infrastructure.
# 3) Подумайте про хранение секретов (kubectl config, DB passwords) — использовать Vault/SSM/Secret Manager.
# 4) Добавьте resources для мониторинга (Prometheus/ServiceMonitor), network policies и RBAC.
