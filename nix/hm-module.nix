{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.ortec;

  env =
    lib.optionalAttrs (cfg.company != null) { ORTEC_COMPANY = cfg.company; }
    // lib.optionalAttrs (cfg.userFile != null) { ORTEC_USER_FILE = cfg.userFile; }
    // lib.optionalAttrs (cfg.passwordFile != null) { ORTEC_PASS_FILE = cfg.passwordFile; };

  # Wrapped rather than exported as session variables, so the paths reach the
  # binaries whatever starts them: a login shell, Claude Code, a systemd unit.
  wrapped = pkgs.symlinkJoin {
    name = "${cfg.package.name}-wrapped";
    paths = [ cfg.package ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = lib.concatMapStrings (bin: ''
      wrapProgram $out/bin/${bin} ${
        lib.concatStrings (lib.mapAttrsToList (k: v: " --set-default ${k} ${lib.escapeShellArg v}") env)
      }
    '') [ "ortec" "ortec-mcp" ];
  };
in
{
  options.programs.ortec = {
    enable = lib.mkEnableOption "the ortec CLI and MCP server";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.ortec-cli;
      defaultText = lib.literalExpression "pkgs.ortec-cli";
      description = ''
        The package to install. The default needs this flake's
        `overlays.default` in `nixpkgs.overlays`.
      '';
    };

    company = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "ACME1";
      description = ''
        The company code you enter in the ORTEC app, passed as $ORTEC_COMPANY.
        Not a secret: it only says which employer's login page to use.
      '';
    };

    userFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/ortec-email";
      description = "File holding the login e-mail address, passed as $ORTEC_USER_FILE.";
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/ortec-password";
      description = "File holding the password, passed as $ORTEC_PASS_FILE.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ (if env == { } then cfg.package else wrapped) ];
  };
}
