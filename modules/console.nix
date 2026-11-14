{ lib, pkgs, ... }:
let
  status = pkgs.writeShellApplication {
    name = "nixlab-status";
    runtimeInputs = [ pkgs.python3 pkgs.iproute2 pkgs.systemd ];
    text = ''exec python3 ${../scripts/console_status.py} "$@"'';
  };
  fetchConfig = pkgs.writeText "nixlab-fastfetch.json" (builtins.toJSON {
    logo = { type = "data"; source = "NIXLAB\nNixOS + Incus"; };
    modules = [
      "title" "separator"
      { type = "custom"; key = "NixLab"; format = lib.removeSuffix "\n" (builtins.readFile ../VERSION); }
      "os" "kernel" "uptime" "cpu" "memory"
      { type = "disk"; folders = "/"; }
      "break"
      { type = "command"; key = ""; text = "${status}/bin/nixlab-status"; timeout = 2500; }
    ];
  });
  fetch = pkgs.writeShellApplication {
    name = "nixlab-fetch";
    runtimeInputs = [ pkgs.fastfetch ];
    text = ''exec fastfetch --config ${fetchConfig} "$@"'';
  };
in {
  environment.systemPackages = [ status fetch pkgs.fastfetch ];
  environment.etc."xdg/fastfetch/config.jsonc".source = fetchConfig;
  programs.bash.completion.enable = lib.mkDefault true;
  programs.bash.blesh.enable = lib.mkDefault true;
  programs.fzf = { keybindings = lib.mkDefault true; fuzzyCompletion = lib.mkDefault true; };
  programs.starship = {
    enable = lib.mkDefault true;
    settings = {
      add_newline = true;
      command_timeout = 250;
      scan_timeout = 50;
      format = "$username@$hostname $directory$git_branch$git_status$nix_shell$cmd_duration$line_break$character";
      username = { show_always = true; style_user = "bold cyan"; style_root = "bold red"; format = "[$user]($style)"; };
      hostname = { ssh_only = false; format = "[$hostname]($style)"; style = "bold green"; };
      directory = { truncation_length = 3; style = "bold blue"; };
      git_branch = { symbol = ""; format = " on [$branch]($style)"; };
      nix_shell = { disabled = false; symbol = "nix"; format = " via [$symbol]($style)"; };
      character = { success_symbol = "[>](bold green)"; error_symbol = "[>](bold red)"; };
    };
  };
  programs.bash.shellAliases = {
    ll = lib.mkDefault "ls -lah --group-directories-first --color=auto";
    failed = lib.mkDefault "systemctl --failed --no-pager";
    nixlab-update = lib.mkDefault "nix run --refresh github:OmarDardery/nixlab#update";
  };
  programs.bash.interactiveShellInit = ''
    HISTCONTROL=ignoreboth
    HISTSIZE=10000
    HISTFILESIZE=20000
    shopt -s histappend checkwinsize
    bind 'set completion-ignore-case on'
    bind 'set show-all-if-ambiguous on'
  '';
  programs.bash.loginShellInit = ''
    if [[ $- == *i* && -t 1 && "$TERM" != dumb && ! -e "$HOME/.hushlogin" && -z "''${NIXLAB_NO_FETCH:-}" ]]; then
      ${fetch}/bin/nixlab-fetch
    fi
  '';
}
