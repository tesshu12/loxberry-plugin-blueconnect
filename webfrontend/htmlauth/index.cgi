#!/usr/bin/perl
# BlueConnect - configuration frontend for LoxBerry
# Uses the native LoxBerry frame (LoxBerry::Web lbheader/lbfooter + %navbar tabs).
# Bilingual (DE/EN); default language = LoxBerry system language, user-switchable.

use strict;
use warnings;
use CGI;
use LoxBerry::System;          # path globals ($lbpconfigdir, ...) + helpers
use LoxBerry::Web;             # lbheader(), lbfooter()
use Config::Simple;
use JSON;
use File::Slurp;
use POSIX qw(strftime);

my $cgi     = CGI->new;
my $version = LoxBerry::System::pluginversion();

our $LANG = 'en';
sub lng { return $LANG eq 'de' ? $_[0] : $_[1]; }   # lng(german, english)

# ── Paths (LoxBerry globals) ─────────────────────────────────────────────────────
my $config_file  = "$lbpconfigdir/pool.cfg";
my $general_file = "$lbsconfigdir/general.json";
my $cache_file   = "/tmp/blueconnect_pool.json";
my $log_file     = "$lbpdatadir/blueconnect.log";
my $script       = "$lbpbindir/fetch_pool.py";

sub esc { my ($s) = @_; $s //= ''; $s =~ s/&/&amp;/g; $s =~ s/</&lt;/g; $s =~ s/>/&gt;/g; return $s; }

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

my $action = $cgi->param('action') // '';

# ── JSON download (must finish before the LoxBerry frame prints anything) ─────────
if ($action eq 'json' && -f $cache_file) {
    my $json = read_file($cache_file, binmode => ':raw');
    print $cgi->header(-type => 'application/json', -charset => 'UTF-8',
                       -attachment => 'blueconnect.json');
    print $json;
    exit;
}

# ── Language: persist a switch, then resolve (saved > system default) ─────────────
my $setlang = $cgi->param('setlang') // '';
if ($setlang eq 'de' || $setlang eq 'en') {
    my $c = Config::Simple->new(syntax => 'ini');
    $c->read($config_file) if -f $config_file;
    $c->param("ui.language", $setlang);
    $c->save($config_file);
}
{
    my $c = Config::Simple->new(syntax => 'ini');
    $c->read($config_file) if -f $config_file;
    my $saved = $c->param("ui.language") // '';
    if ($saved eq 'de' || $saved eq 'en') {
        $LANG = $saved;
    } else {
        my $sys = 'en';
        eval { $sys = lc(LoxBerry::System::lblanguage() // 'en'); };
        $LANG = ($sys =~ /^de/) ? 'de' : 'en';
    }
}

my $message = '';
my $message_err = 0;

# ── Process actions ──────────────────────────────────────────────────────────────
if ($action eq 'save') {
    my $cfg = Config::Simple->new(syntax => 'ini');
    $cfg->read($config_file) if -f $config_file;

    my $new_user = $cgi->param('username') // '';
    my $new_pass = $cgi->param('password') // '';
    my $old_user = $cfg->param("blueconnect.username") // '';

    $cfg->param("blueconnect.username", $new_user);
    if ($new_pass ne '') {
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
    $message = lng('Einstellungen gespeichert. Das Passwort wird beim nächsten Abruf verschlüsselt.',
                   'Settings saved. The password will be encrypted on the next fetch.');
}

if ($action eq 'fetch') {
    my $out = `python3 "$script" 2>&1`;
    if ($? == 0) {
        $message = lng('Daten erfolgreich abgerufen.', 'Data fetched successfully.');
    } else {
        $message = lng('Fehler beim Abruf: ', 'Fetch error: ') . $out;
        $message_err = 1;
    }
}

if ($action eq 'clearlog' && -f $log_file) {
    open(my $fh, '>', $log_file) or warn "Could not clear log: $!";
    close $fh;
    $message = lng('Protokoll geleert.', 'Log cleared.');
}

# ── Active tab ───────────────────────────────────────────────────────────────────
my $active = 'data';
$active = 'config' if $action eq 'save';
$active = 'log'    if $action eq 'clearlog';
$active = 'data'   if $action eq 'fetch';
my $tabparam = $cgi->param('tab') // '';
$active = $tabparam if $tabparam =~ /^(data|config|log)$/;

# ── Load config (fresh, reflects any save above) ─────────────────────────────────
my $cfg = Config::Simple->new(syntax => 'ini');
$cfg->read($config_file) if -f $config_file;

my $username    = $cfg->param("blueconnect.username")       // '';
my $pool_name   = $cfg->param("blueconnect.pool_name")      // '';
my $blue_serial = $cfg->param("blueconnect.blue_serial")    // '';
my $pw_enc      = $cfg->param("blueconnect.password_enc")   // '';
my $pw_plain    = $cfg->param("blueconnect.password_plain") // '';
my $pw_status   = $pw_enc   ? lng('Verschlüsselt gespeichert', 'Encrypted and stored')
                : $pw_plain ? lng('Wird beim nächsten Abruf verschlüsselt', 'Pending encryption (run a fetch)')
                :             lng('Nicht gesetzt', 'Not set');
my $ms_nr       = $cfg->param("loxone.miniserver_nr")   // '';
my $ms_port     = $cfg->param("loxone.miniserver_port") // '7777';
my $interval    = $cfg->param("polling.interval")       // '300';
my $interval_min = int(($interval || 300) / 60);

my $setup_done = ($username ne '' && ($pw_enc ne '' || $pw_plain ne '')) ? 1 : 0;
my ($ms_ip, $ms_name) = get_miniserver($ms_nr);

# ── Load cache ───────────────────────────────────────────────────────────────────
my ($cache, $values, $cpool, $cserial, $last_upd, $meas_upd) = ({}, {}, '', '', '', '');
if (-f $cache_file) {
    eval {
        my $json = read_file($cache_file, binmode => ':utf8');
        $cache   = decode_json($json);
        $values  = $cache->{values}      // {};
        $cpool   = $cache->{pool}        // '';
        $cserial = $cache->{blue_serial} // '';
        my $epoch = $values->{last_update_epoch};
        if (defined $epoch && $epoch =~ /^\d+$/) {
            $last_upd = strftime("%d.%m.%Y %H:%M:%S", localtime($epoch));
        } else {
            $last_upd = $values->{last_update} // '';
        }
        # Time of the last actual measurement (what the app shows as "last updated")
        my $mepoch = $values->{measurement_epoch};
        if (defined $mepoch && $mepoch =~ /^\d+$/) {
            $meas_upd = strftime("%d.%m.%Y %H:%M:%S", localtime($mepoch));
        }
    };
}

# ── Labels / units (language-aware) ──────────────────────────────────────────────
my %units = (
    temperature => ' &deg;C', orp => ' mV', ph => '',
    temperature_current => ' &deg;C', temperature_min => ' &deg;C',
    temperature_max => ' &deg;C', wind_speed_current => ' m/s',
);
my %labels = (
    temperature         => lng('Wassertemperatur','Water temperature'),
    ph                  => lng('pH-Wert','pH value'),
    orp                 => lng('ORP (Redox)','ORP (Redox)'),
    battery_low         => lng('Batterie','Battery'),
    temperature_current => lng('Lufttemperatur','Air temperature'),
    temperature_max     => lng('Lufttemp. max.','Air temp. max.'),
    temperature_min     => lng('Lufttemp. min.','Air temp. min.'),
    wind_speed_current  => lng('Windgeschwindigkeit','Wind speed'),
);

my @device_keys  = grep { exists $values->{$_} } qw(temperature ph orp battery_low);
my @weather_keys = grep { exists $values->{$_} }
                   qw(temperature_current temperature_max temperature_min wind_speed_current);

# A measurement/status row with a coloured dot + optional OK/Warning pill.
sub status_row {
    my ($key) = @_;
    return '' unless exists $values->{$key};
    my $val   = $values->{$key};
    my $label = $labels{$key} // do { (my $l = $key) =~ s/_/ /g; ucfirst $l };
    my ($disp, $pill, $dotcls) = ('', '', 'dot-green');

    if ($key eq 'battery_low') {
        my $low = ($val && $val ne '0') ? 1 : 0;
        $disp   = $low ? lng('Schwach','Low') : 'OK';
        $pill   = $low ? "<span class='pill pill-warn'>" . lng('Achtung','Warning') . "</span>"
                       : "<span class='pill pill-ok'>OK</span>";
        $dotcls = $low ? 'dot-red' : 'dot-green';
    } else {
        my $unit   = $units{$key} // '';
        $disp = "$val$unit";
        my $ok_min = $values->{"${key}_ok_min"};
        my $ok_max = $values->{"${key}_ok_max"};
        if (defined $ok_min && defined $ok_max) {
            my $ok = ($val >= $ok_min && $val <= $ok_max);
            $pill   = $ok ? "<span class='pill pill-ok'>OK</span>"
                          : "<span class='pill pill-warn'>" . lng('Achtung','Warning') . "</span>";
            $dotcls = $ok ? 'dot-green' : 'dot-red';
            $disp  .= " <span style='color:#9b9b9b;font-weight:400;font-size:.85em'>($ok_min&ndash;$ok_max$unit)</span>";
        }
    }
    return "<tr><td><span class='dot $dotcls'></span>" . esc($label) . "</td>"
         . "<td class='tval'>$disp</td><td class='tstat'>$pill</td></tr>\n";
}

# ── Navbar tabs (rendered by lbheader) ───────────────────────────────────────────
our %navbar;
$navbar{10}{Name} = lng('Status & Daten','Status & data'); $navbar{10}{URL} = "index.cgi?tab=data";
$navbar{10}{active} = 1 if $active eq 'data';
$navbar{20}{Name} = lng('Einstellungen','Settings');       $navbar{20}{URL} = "index.cgi?tab=config";
$navbar{20}{active} = 1 if $active eq 'config';
$navbar{30}{Name} = lng('Protokoll','Log');                $navbar{30}{URL} = "index.cgi?tab=log";
$navbar{30}{active} = 1 if $active eq 'log';

# ── Render ───────────────────────────────────────────────────────────────────────
LoxBerry::Web::lbheader("BlueConnect V$version",
    "https://github.com/tesshu12/loxberry-plugin-blueconnect", "help.html", "nojqm");

print <<'STYLE';
<style>
  .bc-wrap{max-width:740px;margin:0 auto}
  .bc-card{background:#fff;border:1px solid #e0e0e0;border-radius:6px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);overflow:hidden;margin-bottom:8px}
  .bc-head{display:flex;align-items:center;gap:14px;
    background:linear-gradient(135deg,#1e88e5,#0d47a1);color:#fff;padding:18px 22px}
  .bc-head .logo{width:50px;height:50px;background:#fff;border-radius:8px;flex:none;
    display:flex;align-items:center;justify-content:center}
  .bc-head h1{margin:0;font-size:1.5em;font-weight:700;color:#fff}
  .bc-head p{margin:2px 0 0;font-size:.85em;opacity:.92}
  .bc-head .lang{margin-left:auto;font-size:.82em;color:#fff;display:flex;align-items:center;gap:6px}
  .bc-head .lang select{background:rgba(255,255,255,.18);color:#fff;border:1px solid rgba(255,255,255,.35);
    border-radius:4px;padding:5px 8px;font-size:.95em}
  .bc-head .lang select option{color:#333}
  .bc-body{padding:18px 22px 22px;color:#333;font-family:"Helvetica Neue",Arial,sans-serif}
  details{border:1px solid #d9e6ef;border-radius:5px;margin:0 0 12px;background:#eaf4fb}
  summary{list-style:none;cursor:pointer;padding:11px 14px;font-size:.9em;color:#1565c0;font-weight:600}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"\25B6";display:inline-block;margin-right:8px;font-size:.7em;transition:transform .15s}
  details[open] summary::before{transform:rotate(90deg)}
  summary .sub{display:block;font-weight:400;color:#5a8fbf;font-size:.92em;margin-top:2px}
  .det-body{padding:2px 16px 14px;font-size:.88em;color:#444;line-height:1.55}
  .det-body ol{margin:6px 0 0;padding-left:20px}
  .det-body code{background:#fff;border:1px solid #ddd;border-radius:3px;padding:1px 5px}
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:6px 0 22px}
  @media(max-width:620px){.tiles{grid-template-columns:repeat(2,1fr)}}
  .tile{background:#f6f6f6;border-left:4px solid #1e88e5;border-radius:0 4px 4px 0;padding:11px 13px;min-height:78px}
  .tile .tl{font-size:.66em;letter-spacing:.08em;color:#9b9b9b;font-weight:700;text-transform:uppercase}
  .tile .tv{margin-top:5px;font-size:1.0em;font-weight:700;color:#333;word-break:break-word}
  h3.sec{font-size:1.05em;color:#333;margin:20px 0 8px}
  table.data{width:100%;border-collapse:collapse}
  table.data th{text-align:left;font-size:.66em;letter-spacing:.08em;color:#9b9b9b;font-weight:700;
    text-transform:uppercase;padding:8px;border-bottom:1px solid #eee}
  table.data td{padding:11px 8px;border-bottom:1px solid #f0f0f0;font-size:.92em}
  table.data td.tval{font-weight:600}
  table.data td.tstat{text-align:right}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:9px;vertical-align:middle}
  .dot-green{background:#7ac143}.dot-red{background:#e53935}
  .pill{display:inline-block;padding:3px 11px;border-radius:11px;font-size:.78em;font-weight:600}
  .pill-ok{background:#e3f2da;color:#3c763d}.pill-warn{background:#fdecea;color:#c0392b}
  .acts{margin-top:14px}
  .act{display:block;width:100%;box-sizing:border-box;text-align:center;
    background:linear-gradient(#fafafa,#eaeaea);border:1px solid #d2d2d2;border-radius:4px;
    padding:13px;margin-top:10px;color:#333 !important;font-size:.95em;font-weight:600;
    cursor:pointer;text-decoration:none;font-family:inherit;line-height:1.2}
  .act:hover{background:linear-gradient(#fff,#e2e2e2);color:#333 !important;text-decoration:none}
  table.cfg{width:100%;border-collapse:collapse}
  table.cfg td{padding:8px 4px;vertical-align:middle}
  table.cfg td:first-child{width:215px;font-weight:600;color:#444}
  table.cfg input,table.cfg select{width:100%;padding:8px 10px;border:1px solid #d2d2d2;border-radius:4px;font-size:1em}
  .hint{font-size:.8em;color:#999;margin-top:3px}
  .btn-save{background:linear-gradient(#1e88e5,#0d47a1);color:#fff !important;border:none;border-radius:4px;
    padding:11px 26px;font-size:1em;font-weight:600;cursor:pointer;text-shadow:none !important}
  .btn-save:hover{color:#fff !important;background:linear-gradient(#1976d2,#0b3d8c)}
  .info{border-radius:5px;padding:10px 14px;margin:6px 0 14px;font-size:.9em}
  .info-green{background:#eef7e9;border:1px solid #cfe8bf;color:#3c763d}
  .info-red{background:#fdecea;border:1px solid #f5c6c0;color:#c0392b;white-space:pre-wrap}
  .info-blue{background:#e8f1fb;border:1px solid #bcd9f3;color:#1565c0}
  .msg-ok{background:#eef7e9;border:1px solid #cfe8bf;color:#3c763d;border-radius:5px;padding:11px 16px;margin:0 0 14px;font-size:.92em}
  .msg-err{background:#fdecea;border:1px solid #f5c6c0;color:#c0392b;border-radius:5px;padding:11px 16px;margin:0 0 14px;font-size:.92em;white-space:pre-wrap}
  .logbox{background:#1e1e1e;color:#d7d7d7;font-family:monospace;font-size:.8em;border-radius:5px;
    padding:12px 14px;height:340px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  .log-err{color:#ef9a9a;font-weight:bold}.log-warn{color:#ffe082}
</style>
STYLE

print "<div class='bc-wrap'>\n<div class='bc-card'>\n";

# Blue card header with pool waves + sun logo + language selector
my $sub  = lng('Pool-Sensordaten (Blue Connect) per UDP an Loxone',
               'Pool sensor data (Blue Connect) via UDP to Loxone');
my $lblL = lng('Sprache:', 'Language:');
my $sel_de = $LANG eq 'de' ? ' selected' : '';
my $sel_en = $LANG eq 'en' ? ' selected' : '';
print <<"CARDHEAD";
<div class="bc-head">
  <div class="logo">
    <svg width="40" height="40" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
      <circle cx="20" cy="17" r="7" fill="#ffd54f"/>
      <g fill="none" stroke="#1e88e5" stroke-width="5" stroke-linecap="round">
        <path d="M6 34 q8 -7 16 0 t16 0 t16 0"/>
        <path d="M6 46 q8 -7 16 0 t16 0 t16 0"/>
        <path d="M6 58 q8 -7 16 0 t16 0 t16 0"/>
      </g>
    </svg>
  </div>
  <div>
    <h1>BlueConnect</h1>
    <p>$sub</p>
  </div>
  <form method="get" action="index.cgi" class="lang">
    <input type="hidden" name="tab" value="$active">
    <label>$lblL</label>
    <select name="setlang" onchange="this.form.submit()">
      <option value="de"$sel_de>Deutsch</option>
      <option value="en"$sel_en>English</option>
    </select>
  </form>
</div>
CARDHEAD

print "<div class='bc-body'>\n";

if ($message) {
    my $cls = $message_err ? 'msg-err' : 'msg-ok';
    print "<div class='$cls'>" . esc($message) . "</div>\n";
}

# ── DATA tab ─────────────────────────────────────────────────────────────────────
if ($active eq 'data') {

    if (!$setup_done) {
        if ($LANG eq 'de') {
            print <<'SETUP_DE';
<details>
  <summary>Erste Einrichtung<span class="sub">Aufklappen für die Schritte in Kurzform</span></summary>
  <div class="det-body"><ol>
    <li>Im Tab <b>Einstellungen</b> Blue-Riiot-E-Mail &amp; Passwort eintragen.</li>
    <li>UDP-Port angeben, <b>Speichern</b>, dann hier auf <b>Jetzt abrufen</b>.</li>
    <li>Pool und Blue-Gerät werden beim ersten Abruf automatisch erkannt.</li>
    <li>In Loxone einen <i>Virtuellen UDP-Eingang</i> anlegen, Port = UDP-Port aus den Einstellungen.</li>
    <li>Pro Wert eine Befehlserkennung anlegen, z.&nbsp;B. <code>temperature=\v</code>,
        <code>ph=\v</code>, <code>orp=\v</code>.</li>
  </ol></div>
</details>
SETUP_DE
        } else {
            print <<'SETUP_EN';
<details>
  <summary>First-time setup<span class="sub">Expand for the quick steps</span></summary>
  <div class="det-body"><ol>
    <li>On the <b>Settings</b> tab, enter your Blue Riiot email &amp; password.</li>
    <li>Set the UDP port, <b>Save</b>, then click <b>Fetch now</b> here.</li>
    <li>Pool and Blue device are detected automatically on the first fetch.</li>
    <li>In Loxone create a <i>Virtual UDP Input</i>, port = the UDP port from Settings.</li>
    <li>Add a command recognition per value, e.g. <code>temperature=\v</code>,
        <code>ph=\v</code>, <code>orp=\v</code>.</li>
  </ol></div>
</details>
SETUP_EN
        }
    }

    my $dash = '&ndash;';
    my $pool = $blue_serial
        ? ($pool_name ? esc($pool_name) : lng('Pool','Pool')) . "<br><span style='font-weight:400;font-size:.82em;color:#888'>" . esc($blue_serial) . "</span>"
        : ($cpool ? esc($cpool) : lng('Noch nicht erkannt','Not detected yet'));
    my $meas = $meas_upd ? $meas_upd : ($last_upd ? $last_upd : $dash);

    print "<div class='tiles'>\n";
    print "  <div class='tile'><div class='tl'>" . lng('Datenquelle','Data source')        . "</div><div class='tv'>Blue Riiot</div></div>\n";
    print "  <div class='tile'><div class='tl'>" . lng('Pool / Gerät','Pool / device')     . "</div><div class='tv'>$pool</div></div>\n";
    print "  <div class='tile'><div class='tl'>" . lng('Letzte Messung','Last measurement') . "</div><div class='tv'>$meas</div></div>\n";
    print "  <div class='tile'><div class='tl'>" . lng('Abrufintervall','Polling interval') . "</div><div class='tv'>${interval_min} min</div></div>\n";
    print "</div>\n";

    if (@device_keys) {
        print "<h3 class='sec'>" . lng('Messwerte','Measurements') . "</h3>\n";
        print "<table class='data'>\n<thead><tr><th>" . lng('Kategorie','Category')
            . "</th><th>" . lng('Wert','Value') . "</th><th style='text-align:right'>"
            . lng('Status','Status') . "</th></tr></thead>\n<tbody>\n";
        print status_row($_) for @device_keys;
        print "</tbody>\n</table>\n";
    } else {
        print "<p class='hint'>" . lng(
            'Noch keine Pooldaten. Im Tab Einstellungen anmelden und unten auf <b>Jetzt abrufen</b> klicken.',
            'No pool data yet. Sign in on the Settings tab and click <b>Fetch now</b> below.') . "</p>\n";
    }

    if (@weather_keys) {
        print "<h3 class='sec'>" . lng('Wetter','Weather') . "</h3>\n<table class='data'><tbody>\n";
        print status_row($_) for @weather_keys;
        print "</tbody></table>\n";
    }

    print "<div class='acts'>\n";
    print "<form method='post' action='index.cgi'><input type='hidden' name='action' value='fetch'>\n";
    print "<button type='submit' class='act'>" . lng('Jetzt abrufen','Fetch now') . "</button></form>\n";
    print "<form method='get' action='index.cgi'><input type='hidden' name='action' value='json'>\n";
    print "<button type='submit' class='act'>" . lng('JSON herunterladen','Download JSON') . "</button></form>\n";
    print "</div>\n";
}

# ── CONFIG tab ───────────────────────────────────────────────────────────────────
if ($active eq 'config') {
    print "<form method='post' action='index.cgi'><input type='hidden' name='action' value='save'>\n";

    print "<h3 class='sec'>" . lng('Blue Riiot Zugangsdaten','Blue Riiot credentials') . "</h3>\n<table class='cfg'>\n";
    print "<tr><td>" . lng('E-Mail-Adresse','Email address') . "</td><td><input type='email' name='username' value='" . esc($username) . "' placeholder='" . lng('ihr','you') . "\@email.de' autocomplete='username'></td></tr>\n";
    print "<tr><td>" . lng('Passwort','Password') . "</td><td><input type='password' name='password' placeholder='" . lng('Passwort (leer = unverändert)','Password (blank = unchanged)') . "' autocomplete='current-password'>"
        . "<div class='hint'>Status: $pw_status</div></td></tr>\n";
    print "</table>\n";
    print "<p class='hint'>" . lng('Pool-ID und Blue-Gerät-Serial werden nach dem ersten Abruf automatisch erkannt.',
                                   'Pool ID and Blue device serial are detected automatically on the first fetch.') . "</p>\n";

    print "<h3 class='sec'>" . lng('Loxone Miniserver','Loxone Miniserver') . "</h3>\n";
    if ($ms_ip) {
        print "<div class='info info-green'>" . lng('Ziel-Miniserver (aus Systemeinstellungen):','Target Miniserver (from system settings):') . " <b>" . esc($ms_name) . "</b> &nbsp;|&nbsp; IP: <b>$ms_ip</b></div>\n";
    } else {
        print "<div class='info info-red'>" . lng('Kein Miniserver in den LoxBerry-Systemeinstellungen gefunden. Bitte unter Einstellungen &rarr; Miniserver konfigurieren.','No Miniserver found in the LoxBerry system settings. Please configure one under Settings &rarr; Miniserver.') . "</div>\n";
    }
    print "<table class='cfg'>\n";
    print "<tr><td>" . lng('UDP-Port','UDP port') . "</td><td><input type='number' name='ms_port' value='" . esc($ms_port) . "' min='1' max='65535'><div class='hint'>" . lng('Port Ihres Loxone Virtuellen UDP-Eingangs.','Port of your Loxone Virtual UDP Input.') . "</div></td></tr>\n";
    print "<tr><td>" . lng('Abrufintervall (Sek.)','Polling interval (sec.)') . "</td><td><input type='number' name='interval' value='" . esc($interval) . "' min='60' max='86400'><div class='hint'>" . lng('Das Blue-Gerät sendet ~alle 72 Min.','The Blue device sends roughly every 72 min.') . "</div></td></tr>\n";
    print "</table>\n";
    print "<div style='margin-top:16px'><button type='submit' class='btn-save'>" . lng('Speichern','Save') . "</button></div>\n";
    print "</form>\n";
}

# ── LOG tab ──────────────────────────────────────────────────────────────────────
if ($active eq 'log') {
    print "<h3 class='sec'>" . lng('Protokoll','Log') . "</h3>\n";
    if (-f $log_file) {
        my @lines = read_file($log_file, binmode => ':utf8', err_mode => 'quiet');
        my @tail  = @lines > 80 ? @lines[-80..-1] : @lines;
        print "<div class='logbox'>";
        for my $line (reverse @tail) {
            chomp $line;
            my $cls = '';
            $cls = 'log-err'  if $line =~ /\[ERROR\]|\[CRITICAL\]/i;
            $cls = 'log-warn' if $line =~ /\[WARNING\]/i;
            my $safe = esc($line);
            print $cls ? "<span class='$cls'>$safe</span>\n" : "$safe\n";
        }
        print "</div>\n";
        print "<form method='post' action='index.cgi' style='margin-top:10px'><input type='hidden' name='action' value='clearlog'>\n";
        print "<button type='submit' class='act' style='width:auto;padding:8px 16px'>" . lng('Protokoll leeren','Clear log') . "</button></form>\n";
    } else {
        print "<p class='hint'>" . lng('Noch kein Protokoll &ndash; es wird beim ersten Abruf erstellt.','No log yet &ndash; it is created on the first fetch.')
            . "<br>" . lng('Pfad:','Path:') . " <code>" . esc($log_file) . "</code></p>\n";
    }
}

print "</div>\n</div>\n</div>\n";   # bc-body, bc-card, bc-wrap

LoxBerry::Web::lbfooter();
exit;
