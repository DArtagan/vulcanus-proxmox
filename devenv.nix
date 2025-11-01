{
  pkgs,
  ...
}:

{
  packages = [
    pkgs.git
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
    trim-trailing-whitespace.enable = true;
  };
}
