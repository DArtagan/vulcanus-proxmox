{
  description = "NixOS hosts for the vulcanus estate, deployed with colmena.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Upstream main, not nixpkgs' v0.4.0, the last tag (May 2023). flake.lock
    # pins the revision. See docs/nixos.md.
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

      # One definition per host, feeding every output below; see
      # docs/nixos.md. `deployment` is here, not in the module, because it is a
      # colmena option, which a plain nixosSystem rejects.
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
      # The hive, which the pinned colmena reads through `colmenaHive` below.
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

      # What the pinned colmena evaluates; it errors without it.
      colmenaHive = colmena.lib.makeHive self.outputs.colmena;

      # The same systems, for `nixos-rebuild --flake` and the like.
      nixosConfigurations = builtins.mapAttrs (
        _: host:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs.inputs = self.inputs;
          modules = [ host.module ];
        }
      ) hosts;

      # LXC templates, for creating a container. colmena owns it after that.
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
