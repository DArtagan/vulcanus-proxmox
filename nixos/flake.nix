{
  description = "NixOS hosts for the vulcanus estate, deployed with colmena.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    colmena = {
      url = "github:nix-community/colmena";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Young, with one maintainer, so pinned to a revision and moved deliberately:
    # `nix flake update` cannot move it. Its nixpkgs is left its own, so its
    # packages are the builds its binary cache holds. See docs/nixos.md.
    nixos-pbs.url = "github:AWildLeon/nixos-pbs/57c3aafe739be5540935ce4c1a129073a9f9f09d";
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
      inherit (nixpkgs) lib;

      hosts = {
        restic-repository = {
          module = ./hosts/restic-repository;
          address = "192.168.0.108";
        };
      };

      # What every host module receives besides the usual module arguments.
      hostArgs = host: {
        inherit (self) inputs;
        inherit (host) address;
      };
    in
    {
      colmenaHive = colmena.lib.makeHive (
        {
          meta = {
            nixpkgs = nixpkgs.legacyPackages.${system};
            nodeSpecialArgs = lib.mapAttrs (_: hostArgs) hosts;
          };
        }
        // lib.mapAttrs (_: host: {
          imports = [ host.module ];
          deployment = {
            targetHost = host.address;
            targetUser = "root";
          };
        }) hosts
      );

      # NixOS containers that another host runs and deploys: PBS #2 is mini-nas's
      # `containers.pbs-offsite`, built from here. See docs/nixos.md.
      nixosConfigurations.pbs-offsite = lib.nixosSystem {
        inherit system;
        specialArgs = { inherit (self) inputs; };
        modules = [ ./hosts/pbs-offsite ];
      };

      # LXC templates, for creating a container; colmena owns it after that.
      packages.${system} = lib.mapAttrs (
        _: host:
        (lib.nixosSystem {
          inherit system;
          specialArgs = hostArgs host;
          modules = [ host.module ];
        }).config.system.build.tarball
      ) hosts;
    };
}
