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
  };
}
