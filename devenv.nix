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
    python3
    sops
    talosctl
  ];

  languages = {
    ansible.enable = true;
    nix.enable = true;
    opentofu.enable = true;
  };

  claude.code.enable = true;

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
      # Matches beets-flask and the CronJob, so anything done from this shell
      # writes library.blb as the uid that owns it. See cron-job.yaml.
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
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
              # The config sets `pluginpath: /config/beetsplug`; without this
              # every beet command prints an error about audiobook_genre.
              - key: beets-flask-plugin-audiobook-genre.py
                path: beetsplug/audiobook_genre.py
        - name: library
          persistentVolumeClaim:
            claimName: beets-library-pvc
        - name: audio
          persistentVolumeClaim:
            claimName: audio-rw-beets-pvc
    EOF
    kubectl wait -n apps --for=condition=Ready pod/beets-manual --timeout=180s

    kubectl exec -n apps -it beets-manual -- sh
  '';

  scripts.mb-seed.exec = ''
    # Hands the audiobook backlog to MusicBrainz's release editor one book at a
    # time, with every field pre-filled from the file's own tags.
    #
    # Almost none of these books are in MusicBrainz — 15 ASINs sampled against
    # the web service returned no hits — so they have to be created rather than
    # matched. The Audible rips carry a tone/m4b-tool tag set that already holds
    # everything the release editor asks for, which is what makes seeding worth
    # building instead of typing 479 releases by hand.
    #
    # The manifest is regenerated inside the cluster, where the audio share is
    # mounted, so nothing needs copying in. It is refreshed on every run: the
    # inbox changes as books are imported and deleted, and a stale manifest
    # would offer books that are no longer there.
    set -eu

    here="${config.git.root}/tools/mb-seed"

    pod=$(kubectl get pod -n apps -l app=beets-flask \
      -o jsonpath='{.items[0].metadata.name}')
    if [ -z "$pod" ]; then
      echo "beets-flask is not running; it holds the audio mount." >&2
      exit 1
    fi

    echo "==> Reading /audio/import via $pod"
    kubectl exec -i -n apps -c beets-flask "$pod" -- \
      /venv/bin/python - /audio/import \
      < "$here/manifest.py" > "$here/manifest.json.new"

    # Truncated output from kubectl exec is silent, and a half-written manifest
    # would present as a short queue rather than an error.
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" \
         "$here/manifest.json.new"; then
      echo "Manifest came back truncated or invalid; not replacing." >&2
      exit 1
    fi
    mv "$here/manifest.json.new" "$here/manifest.json"

    # Any arguments narrow the queue by substring, which is how a batch is
    # picked: `mb-seed Goroth Stain Jackson`, or `mb-seed Cradle`.
    # The seeder reaches back into the pod on demand for cover art: 463 covers
    # at a median 553 KiB would be 250 MB of manifest, and the manifest crosses
    # the same kubectl exec pipe that truncates silently.
    MB_SEED_POD="$pod" python3 "$here/seed.py" "$here/manifest.json" "$@"
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
