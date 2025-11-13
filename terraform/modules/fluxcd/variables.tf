variable "github_owner" {
  type = string
  description = "Github account name."
}

variable "repository_name" {
  type = string
  description = "Name of the repository on Github."
}

variable "branch" {
  type = string
  default = "main"
  description = "Git branch name."
}

variable "target_path" {
  type = string
  description = "Directory path in the repo, under which to put the kubernetes manifests."
}

variable "kubeconfig_path" {
  type = string
  description = "Filepath to the kubeconfig."
}

variable "commit_author" {
  type = string
  description = "Name of the person associated with that Github token."
}

variable "commit_email" {
  type = string
  description = "Email of the person associated with that Github token."
}
