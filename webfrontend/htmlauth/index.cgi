#!/usr/bin/perl
# BlueConnect - configuration frontend for LoxBerry
use strict;
use warnings;
use CGI;
use CGI::Carp qw(fatalsToBrowser);
use Config::Simple;
use JSON;
use File::Slurp;
use POSIX qw(strftime);
use Encode qw(decode_utf8);

my $cgi = CGI->new;

# ── Paths ───────────────────────────────────────────────────────────────────────
# LoxBerry sets LBPLUGINDIR to the plugin's config dir (config/plugins/<name>)
my $plugin_dir   = $ENV{LBPLUGINDIR} // "/opt/loxberry/config/plugins/blueconnect";
my $config_file  = "$plugin_dir/../../../config/plugins/blueconnect/pool.cfg";
my $general_file = "$plugin_dir/../../../config/system/general.json";
my $cache_file   = "/tmp/blueconnect_pool.json";
my $log_file     = "$plugin_dir/../../../data/plugins/blueconnect/blueconnect.log";
my $script       = "$plugin_dir/../../../bin/plugins/blueconnect/fetch_pool.py";

# ── Read Miniserver from LoxBerry system settings ────────────────────────────────
sub get_miniserver {
    my ($nr) = @_;
    return ('', '') unless -f $general_file;
    my $data = eval { decode_json(read_file($general_file, binmode => ':utf8')) };
    return ('', '') unless $data && $data->{Miniserver};
    my $ms = $data->{Miniserver};
    my @keys = sort keys %$ms;
    my $key  = ($nr && exists $ms->{$nr}) ? $nr : $keys[0];
    return ('', '') unless defined $key;
    return ($ms->{$key}{Ipaddress} // '', $ms->{$key}{Name} // '');
}

# ── Process action ───────────────────────────────────────────────────────────────
my $action  = $cgi->param('action') // '';
my $message = '';

if ($action eq 'save') {
    my $cfg = Config::Simple->new(syntax => 'ini');
    $cfg->read($config_file) if -f $config_file;

    my $new_user = $cgi->param('username') // '';
    my $new_pass = $cgi->param('password') // '';
    my $old_user = $cfg->param("blueconnect.username") // '';

    $cfg->param("blueconnect.username", $new_user);

    if ($new_pass ne '') {
        # Store plaintext in password_plain - Python encrypts it on next run
        $cfg->param("blueconnect.password_plain", $new_pass);
        $cfg->param("blueconnect.password_enc",   '');
    }

    # On user/password change: clear cached device data -> re-detection
    if ($new_user ne $old_user || $new_pass ne '') {
        $cfg->param("blueconnect.pool_id",     '');
        $cfg->param("blueconnect.pool_name",   '');
        $cfg->param("blueconnect.blue_serial", '');
    }

    $cfg->param("loxone.miniserver_port", $cgi->param('ms_port')  // '7777');
    $cfg->param("polling.interval",       $cgi->param('interval') // '300');
    $cfg->save($config_file);
    $message = 'Settings saved. The password will be encrypted on the next fetch.';
}

if ($action eq 'fetch') {
    my $out = `python3 "$script" 2>&1`;
    $message = $? == 0
        ? 'Data fetched successfully.'
        : "Fetch error: $out";
}

if ($action eq 'clearlog' && -f $log_file) {
    open(my $fh, '>', $log_file) or warn "Could not clear log: $!";
    close $fh;
    $message = 'Log cleared.';
}

# ── Active tab ───────────────────────────────────────────────────────────────────
my $active = 'data';
$active = 'config' if $action eq 'save';
$active = 'log'    if $action eq 'clearlog';
$active = 'data'   if $action eq 'fetch';
my $tabparam = $cgi->param('tab') // '';
$active = $tabparam if $tabparam =~ /^(data|config|log)$/;

# ── Load config ──────────────────────────────────────────────────────────────────
my $cfg = Config::Simple->new(syntax => 'ini');
$cfg->read($config_file) if -f $config_file;

my $username    = $cfg->param("blueconnect.username")       // '';
my $pool_name   = $cfg->param("blueconnect.pool_name")      // '';
my $blue_serial = $cfg->param("blueconnect.blue_serial")    // '';
my $pw_enc      = $cfg->param("blueconnect.password_enc")   // '';
my $pw_plain    = $cfg->param("blueconnect.password_plain") // '';
my $pw_status   = $pw_enc   ? 'Encrypted and stored'
                : $pw_plain ? 'Pending encryption (run a fetch once)'
                :             'Not set';
my $ms_nr       = $cfg->param("loxone.miniserver_nr")   // '';
my $ms_port     = $cfg->param("loxone.miniserver_port") // '7777';
my $interval    = $cfg->param("polling.interval")       // '300';

my ($ms_ip, $ms_name) = get_miniserver($ms_nr);

# ── Load cache ───────────────────────────────────────────────────────────────────
my $cache    = {};
my $values   = {};
my $last_upd = '';
if (-f $cache_file) {
    eval {
        my $json = read_file($cache_file, binmode => ':utf8');
        $cache   = decode_json($json);
        $values  = $cache->{values} // {};
        # Format the timestamp in LoxBerry's local timezone (readable form).
        # Prefer the epoch value; localtime() uses the system TZ.
        my $epoch = $values->{last_update_epoch};
        if (defined $epoch && $epoch =~ /^\d+$/) {
            $last_upd = strftime("%d.%m.%Y %H:%M:%S", localtime($epoch));
        } else {
            $last_upd = $values->{last_update} // '';
        }
    };
}

my %units = (
    temperature         => ' &deg;C',
    temperature_current => ' &deg;C',
    temperature_min     => ' &deg;C',
    temperature_max     => ' &deg;C',
    orp                 => ' mV',
    battery             => ' %',
    tds                 => ' ppm',
    fcl                 => ' mg/l',
    ph                  => '',
    wind_speed_current  => ' m/s',
);
my %labels = (
    temperature         => 'Water temperature',
    orp                 => 'ORP (Redox)',
    ph                  => 'pH value',
    tds                 => 'TDS',
    fcl                 => 'Free chlorine',
    battery             => 'Battery',
    battery_low         => 'Battery',
    temperature_current => 'Air temperature',
    temperature_max     => 'Air temp. max.',
    temperature_min     => 'Air temp. min.',
    wind_speed_current  => 'Wind speed',
);

my @device_keys  = grep { exists $values->{$_} && $_ !~ /ok_min|ok_max|last_update/ }
                   qw(temperature ph orp tds fcl battery battery_low);
my @weather_keys = grep { exists $values->{$_} }
                   qw(temperature_current temperature_max temperature_min wind_speed_current);

sub value_row {
    my ($key) = @_;
    my $val   = $values->{$key};
    return '' unless defined $val;
    my $label  = $labels{$key} // do { (my $l = $key) =~ s/_/ /g; ucfirst $l };

    # Battery is only available as a low-battery flag (0 = OK, 1 = low).
    if ($key eq 'battery_low') {
        my $low    = $val ? 1 : 0;
        my $text   = $low ? 'Low' : 'OK';
        my $status = $low ? ' status-warn' : ' status-ok';
        return "<div class='value-card$status'>"
             . "<span class='vlabel'>$label</span>"
             . "<span class='vval'>$text</span>"
             . "</div>\n";
    }

    my $unit   = $units{$key}  // '';
    my $ok_min = $values->{"${key}_ok_min"};
    my $ok_max = $values->{"${key}_ok_max"};
    my $status = '';
    if (defined $ok_min && defined $ok_max) {
        $status = ($val < $ok_min || $val > $ok_max) ? ' status-warn' : ' status-ok';
    }
    my $range = (defined $ok_min && defined $ok_max)
        ? "<span class='range'>($ok_min - $ok_max$unit)</span>" : '';
    return "<div class='value-card$status'>"
         . "<span class='vlabel'>$label</span>"
         . "<span class='vval'>$val$unit $range</span>"
         . "</div>\n";
}

print $cgi->header(-charset => 'UTF-8', -type => 'text/html');
print <<'HTMLHEAD';
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BlueConnect</title>
  <link rel="stylesheet" href="/system/htmlauth/css/loxberry.min.css">
  <style>
    body{font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:16px}
    h2{color:#1565C0;border-bottom:2px solid #1565C0;padding-bottom:6px}
    h3{color:#37474F;margin-top:24px}
    .topbar{display:flex;align-items:center;gap:12px;margin-bottom:8px}
    .tabs{display:flex;gap:4px;border-bottom:2px solid #1565C0;margin-bottom:16px}
    .tab{padding:10px 22px;cursor:pointer;border:1px solid #CFD8DC;border-bottom:none;
      border-radius:6px 6px 0 0;background:#ECEFF1;color:#546E7A;font-weight:bold;
      font-size:0.95em;margin-bottom:-2px}
    .tab:hover{background:#E3F2FD}
    .tab.active{background:#1565C0;color:#fff;border-color:#1565C0}
    .panel{display:none}
    .panel.active{display:block}
    .value-card{background:#F5F5F5;border-left:4px solid #90CAF9;border-radius:6px;
      padding:10px 16px;margin:6px 0;display:flex;justify-content:space-between;align-items:center}
    .value-card.status-ok{border-left-color:#66BB6A}
    .value-card.status-warn{border-left-color:#FFA726}
    .vlabel{font-weight:bold;color:#37474F}
    .vval{font-size:1.15em;color:#1565C0}
    .range{font-size:0.78em;color:#90A4AE;margin-left:6px}
    .device-info{background:#E3F2FD;border-radius:6px;padding:10px 16px;margin:8px 0;
      font-size:0.9em;color:#37474F}
    .device-info span{font-weight:bold;color:#1565C0}
    .msg-ok{background:#E8F5E9;border:1px solid #A5D6A7;border-radius:6px;
      padding:10px 16px;margin:12px 0;color:#2E7D32}
    .msg-err{background:#FFEBEE;border:1px solid #EF9A9A;border-radius:6px;
      padding:10px 16px;margin:12px 0;color:#C62828}
    table.cfg{width:100%;border-collapse:collapse}
    table.cfg td{padding:8px 6px;vertical-align:middle}
    table.cfg td:first-child{width:200px;font-weight:bold;color:#37474F}
    table.cfg input[type=text],table.cfg input[type=email],
    table.cfg input[type=password],table.cfg input[type=number]{
      width:100%;padding:7px 10px;border:1px solid #CFD8DC;
      border-radius:4px;font-size:1em;box-sizing:border-box}
    .btn{background:#1565C0;color:#fff;border:none;border-radius:4px;
      padding:10px 24px;font-size:1em;cursor:pointer;margin-right:8px;text-decoration:none;
      display:inline-block}
    .btn:hover{background:#0D47A1}
    .btn-sec{background:#546E7A}
    .btn-sec:hover{background:#37474F}
    .section{background:#fff;border:1px solid #CFD8DC;border-radius:8px;
      padding:16px 20px;margin-bottom:20px}
    .no-data{color:#90A4AE;font-style:italic}
    .timestamp{color:#90A4AE;font-size:0.82em}
    .log-box{background:#263238;color:#CFD8DC;font-family:monospace;font-size:0.82em;
      border-radius:6px;padding:12px 14px;height:360px;min-height:120px;
      resize:vertical;overflow:auto;white-space:pre-wrap;word-break:break-all}
    .log-err{color:#EF9A9A;font-weight:bold}
    .log-warn{color:#FFE082}
    .log-info{color:#CFD8DC}
  </style>
</head>
<body>
<div class="topbar">
  <a href="/admin/index.cgi" class="btn btn-sec">&larr; Back</a>
</div>
<h2>BlueConnect</h2>
HTMLHEAD

if ($message) {
    my $cls = $message =~ /error/i ? 'msg-err' : 'msg-ok';
    print "<div class='$cls'>$message</div>\n";
}

# ── Tab navigation ───────────────────────────────────────────────────────────────
sub tab_class { my ($t) = @_; return $active eq $t ? 'tab active' : 'tab'; }
sub panel_class { my ($t) = @_; return $active eq $t ? 'panel active' : 'panel'; }

print "<div class='tabs'>\n";
print "  <div class='" . tab_class('data')   . "' data-tab='data'>Data</div>\n";
print "  <div class='" . tab_class('config') . "' data-tab='config'>Config</div>\n";
print "  <div class='" . tab_class('log')    . "' data-tab='log'>Log</div>\n";
print "</div>\n";

# ── Panel: DATA ──────────────────────────────────────────────────────────────────
print "<div class='" . panel_class('data') . "' id='panel-data'>\n";
print "<div class='section'>\n<h3>Device status</h3>\n";

if ($blue_serial) {
    print "<div class='device-info'>Pool: <span>$pool_name</span> &nbsp;|&nbsp; Blue device: <span>$blue_serial</span></div>\n";
} else {
    print "<div class='device-info'>Device not detected yet - save your credentials and click <em>Fetch now</em>.</div>\n";
}

print "<p class='timestamp'>Last update: $last_upd</p>\n" if $last_upd;

if (@device_keys) {
    print "<h3>Device measurements</h3>\n";
    print value_row($_) for @device_keys;
} else {
    print "<p class='no-data'>No device data yet. Click <em>Fetch now</em>.</p>\n";
}

if (@weather_keys) {
    print "<h3>Weather</h3>\n";
    print value_row($_) for @weather_keys;
}

print <<'FETCHBTN';
<form method="post" style="margin-top:16px">
  <input type="hidden" name="action" value="fetch">
  <button type="submit" class="btn btn-sec">Fetch now</button>
</form>
</div>
</div>
FETCHBTN

# ── Panel: CONFIG ────────────────────────────────────────────────────────────────
print "<div class='" . panel_class('config') . "' id='panel-config'>\n";
print <<'CFGFORM';
<div class='section'>
<h3>Blue Riiot credentials</h3>
<form method="post">
  <input type="hidden" name="action" value="save">
  <table class="cfg">
CFGFORM

print "    <tr><td>Email address</td>\n";
print "    <td><input type='email' name='username' value='$username' placeholder='you\@email.com' autocomplete='username'></td></tr>\n";
print "    <tr><td>Password</td><td>\n";
print "      <input type='password' name='password' placeholder='Enter password (blank = unchanged)' autocomplete='current-password'>\n";
print "      <div style='margin-top:4px;font-size:0.82em;color:#546E7A'>Status: $pw_status</div>\n";
print "    </td></tr>\n";

print <<'CFGMID';
  </table>
  <p style="font-size:0.85em;color:#90A4AE">
    Pool ID and Blue device serial are detected automatically on the first fetch.
  </p>
  <h3>Loxone Miniserver</h3>
CFGMID

if ($ms_ip) {
    print "  <div class='device-info'>Target Miniserver (from system settings): <span>$ms_name</span> &nbsp;|&nbsp; IP: <span>$ms_ip</span></div>\n";
} else {
    print "  <div class='msg-err'>No Miniserver found in LoxBerry system settings. Configure one under Settings &rarr; Miniserver.</div>\n";
}

print <<'MSMID';
  <table class="cfg">
MSMID

print "    <tr><td>UDP port</td><td><input type='number' name='ms_port' value='$ms_port' min='1' max='65535'>\n";
print "      <span style='font-size:0.85em;color:#90A4AE'>&nbsp;Port of your Loxone Virtual UDP Input</span></td></tr>\n";

print <<'CFGBOT';
  </table>
  <h3>Settings</h3>
  <table class="cfg">
CFGBOT

print "    <tr><td>Polling interval (sec.)</td><td><input type='number' name='interval' value='$interval' min='60' max='86400'>\n";
print "      <span style='font-size:0.85em;color:#90A4AE'>&nbsp;The Blue device sends roughly every 72 min.</span></td></tr>\n";

print <<'CFGEND';
  </table>
  <div style="margin-top:16px">
    <button type="submit" class="btn">Save</button>
  </div>
</form>
</div>
</div>
CFGEND

# ── Panel: LOG ───────────────────────────────────────────────────────────────────
print "<div class='" . panel_class('log') . "' id='panel-log'>\n";
print "<div class='section'>\n<h3>Log</h3>\n";

if (-f $log_file) {
    my @lines = read_file($log_file, binmode => ':utf8', err_mode => 'quiet');
    my @tail  = @lines > 80 ? @lines[-80..-1] : @lines;
    print "<div class='log-box'>";
    for my $line (reverse @tail) {
        chomp $line;
        my $cls = 'log-info';
        $cls = 'log-err'  if $line =~ /\[ERROR\]|\[CRITICAL\]/i;
        $cls = 'log-warn' if $line =~ /\[WARNING\]/i;
        (my $safe = $line) =~ s/</&lt;/g;
        $safe =~ s/>/&gt;/g;
        print "<span class='$cls'>$safe</span>\n";
    }
    print "</div>\n";
    print <<'LOGBTN';
<form method="post" style="margin-top:10px">
  <input type="hidden" name="action" value="clearlog">
  <button type="submit" class="btn btn-sec" style="font-size:0.85em;padding:6px 14px">Clear log</button>
</form>
LOGBTN
} else {
    print "<p class='no-data'>No log yet - it is created on the first fetch.</p>\n";
    print "<p class='no-data' style='font-size:0.82em'>Path: <code>$log_file</code></p>\n";
}

print "</div>\n</div>\n";

# ── Tab switching script ─────────────────────────────────────────────────────────
print <<'TABJS';
<script>
(function(){
  var tabs = document.querySelectorAll('.tab');
  function activate(name){
    document.querySelectorAll('.tab').forEach(function(t){
      t.classList.toggle('active', t.dataset.tab === name);
    });
    document.querySelectorAll('.panel').forEach(function(p){
      p.classList.toggle('active', p.id === 'panel-' + name);
    });
    if (history.replaceState) {
      var u = new URL(window.location);
      u.searchParams.set('tab', name);
      history.replaceState(null, '', u);
    }
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ activate(t.dataset.tab); });
  });
})();
</script>
TABJS

print "</body>\n</html>\n";
