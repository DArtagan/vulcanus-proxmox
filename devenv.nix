{
  config,
  pkgs,
  ...
}:

{
  env = {
    # TODO: eliminate .env file, move contents to here and sops
    KUBECONFIG = "${config.git.root}/.kubeconfig";
    TALOSCONFIG = "${config.git.root}/.talosconfig";
  };

  packages = with pkgs; [
    fluxcd
    gh
    git
    k9s
    kubectl
    sops
    talosctl
  ];

  languages = {
    ansible.enable = true;
    nix.enable = true;
    opentofu.enable = true;
  };

  dotenv.enable = true;

  git-hooks.hooks = {
    end-of-file-fixer.enable = true;
    deadnix.enable = true;
    flake-checker.enable = true;
    nixfmt-rfc-style.enable = true;
    shellcheck.enable = true;
    statix.enable = true;
    tflint.enable = true;
    trim-trailing-whitespace.enable = true;
    terraform-no-align-equals = {
      enable = true;
      name = "terraform-no-align-equals";
      description = "Remove aligned equals signs from Terraform argument assignments";
      entry = toString (
        pkgs.writeShellScript "terraform-no-align-equals" ''
          for file in "$@"; do
            sed -i -E 's/^([[:space:]]+[a-zA-Z_][a-zA-Z0-9_-]*)[[:space:]]{2,}=[[:space:]]*/\1 = /g' "$file"
          done
        ''
      );
      files = "\\.tf$";
      language = "system";
      pass_filenames = true;
    };
  };
}
