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

  scripts.beets-shell.exec = ''
    # An interactive beets CLI session against the real library, for the things
    # the beets-flask web UI does not cover (bulk `beet modify`, `beet mbsync`,
    # library surgery).
    #
    # beets-flask is scaled to zero first. Both it and this job mount
    # beets-library-pvc, and ReadWriteOnce restricts to one *node*, not one pod
    # — so Kubernetes will happily let both run, and two beets processes will
    # then write the same SQLite file. Reads would be safe; writes are not.
    #
    # Scaling works against Flux because deployment.yaml sets no `replicas`
    # field, so server-side apply never claims it and the reconciler leaves it
    # alone.
    set -eu

    cleanup() {
      echo "==> Cleaning up"
      kubectl delete pod -n apps beets-manual --ignore-not-found
      kubectl scale -n apps deploy/beets-flask --replicas=1
    }
    trap cleanup EXIT INT TERM

    echo "==> Scaling beets-flask down"
    kubectl scale -n apps deploy/beets-flask --replicas=0
    kubectl wait -n apps --for=delete pod -l app=beets-flask --timeout=120s

    echo "==> Starting an ad-hoc beets pod"
    kubectl delete pod -n apps beets-manual --ignore-not-found
    kubectl apply -f - <<'EOF'
    apiVersion: v1
    kind: Pod
    metadata:
      name: beets-manual
      namespace: apps
    spec:
      restartPolicy: Never
      containers:
        - name: beets
          image: linuxserver/beets:2.13.1
          # Not the CronJob's `beet import` command: this pod exists to be
          # exec'd into. (`kubectl create job --from=cronjob/beets-import`
          # would start a full quiet import the moment it scheduled.)
          command: ["sleep", "infinity"]
          volumeMounts:
            - name: config
              mountPath: /config/
              readOnly: true
            - name: library
              mountPath: /library/
            - name: audio
              mountPath: /audio/
      volumes:
        - name: config
          configMap:
            name: beets-config-map
            items:
              - key: config-beets.yaml
                path: config.yaml
              - key: genre_whitelist.txt
                path: genre_whitelist.txt
        - name: library
          persistentVolumeClaim:
            claimName: beets-library-pvc
        # audio-rw-pvc, not audio-rw-beets-pvc: this image runs as root, which
        # is what the unmapped mount expects.
        - name: audio
          persistentVolumeClaim:
            claimName: audio-rw-pvc
    EOF
    kubectl wait -n apps --for=condition=Ready pod/beets-manual --timeout=180s

    kubectl exec -n apps -it beets-manual -- sh
  '';

  dotenv.enable = true;

  git-hooks.hooks = {
    end-of-file-fixer.enable = true;
    deadnix.enable = true;
    flake-checker.enable = true;
    nixfmt.enable = true;
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
