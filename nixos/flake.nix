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
