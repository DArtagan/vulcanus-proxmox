{
  description = "NixOS hosts for the vulcanus estate, deployed with colmena.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Taken from upstream rather than from nixpkgs, which ships refs/tags/v0.4.0
    # -- the May 2023 release, and the last one tagged. Upstream has since fixed
    # things a deployment relies on, including `--evaluator streaming` being a
    # no-op. flake.lock is the pin: "tracking main" here means one revision that
    # changes only when someone runs `nix flake update`.
    colmena = {
      url = "github:nix-community/colmena";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      colmena,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # One definition per host, feeding both the colmena node and the image
      # build below. They must not diverge: an image built from different
      # modules than the deployment expects is how a host ends up unmanageable
      # the first time anyone tries to change it.
      #
      # `deployment` lives here rather than in the host module because it is a
      # colmena option, and the plain nixosSystem that builds the image rejects
      # options it has never heard of.
      hosts = {
        restic-repository = {
          module = ./hosts/restic-repository;
          deployment = {
            targetHost = "192.168.0.108";
            targetUser = "root";
          };
        };
      };
    in
    {
      # colmena 0.4.0 reads `colmena`; newer versions prefer `colmenaHive` but
      # still accept this, so it stays the one definition both understand.
      colmena = {
        meta = {
          nixpkgs = pkgs;
          specialArgs.inputs = self.inputs;
        };
      }
      // builtins.mapAttrs (_: host: {
        imports = [ host.module ];
        inherit (host) deployment;
      }) hosts;

      # LXC templates to feed Proxmox when first creating a container. After
      # that, colmena owns the host and these are only rebuilt to recreate it.
      packages.${system} = builtins.mapAttrs (
        _: host:
        (nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs.inputs = self.inputs;
          modules = [ host.module ];
        }).config.system.build.tarball
      ) hosts;

      devShells.${system}.default = pkgs.mkShell {
        packages = [ colmena.packages.${system}.colmena ];
      };
    };
}
