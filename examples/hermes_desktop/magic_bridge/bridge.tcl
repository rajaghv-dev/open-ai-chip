# bridge.tcl -- read-only view server inside Magic's own Tcl interpreter (loaded with `source` by start_magic.sh).
# One request per line on a TCP socket, one reply line: "OK <text>" or "ERR <text>" (newlines in the text are
# escaped as \n). Text protocol, NOT eval: the first word picks one proc from the allow-list below and every other
# word must match a strict pattern, so received text never reaches Tcl's evaluator. There is no save, writeall,
# gds write, cif write, shell or file-open command here; the only file written is a PNM under build/agent/.
# Env: MAGIC_BRIDGE_PORT (default 8766), MAGIC_BRIDGE_REPO (repo root, GDS must live under it),
#      MAGIC_BRIDGE_TOKEN (optional: every line must then start with it).
# Commands: PING | LOAD <gds> <top> | VIEW <x1> <y1> <x2> <y2> (um) | FULL | SEE <layers> | DRC | FIND <label>
#           | MEASURE <x1> <y1> <x2> <y2> | PLOT <pnm> [width] | STATE | QUIT
# Docs: examples/hermes_desktop/magic_bridge/README.md, docs/HERMES_DESKTOP.md

set ::bridge_port 8766
if {[info exists ::env(MAGIC_BRIDGE_PORT)]} { set ::bridge_port $::env(MAGIC_BRIDGE_PORT) }
set ::bridge_repo ""
if {[info exists ::env(MAGIC_BRIDGE_REPO)]} { set ::bridge_repo $::env(MAGIC_BRIDGE_REPO) }
set ::bridge_token ""
if {[info exists ::env(MAGIC_BRIDGE_TOKEN)]} { set ::bridge_token $::env(MAGIC_BRIDGE_TOKEN) }
set ::bridge_allowed {PING LOAD VIEW FULL SEE DRC FIND MEASURE PLOT STATE QUIT}

proc bridge_word_ok {w} { return [regexp {^(?:[A-Za-z0-9_./,*<>:+=@-]|\[|\]){1,250}$} $w] }
proc bridge_num {w} {
    if {![string is double -strict $w] || abs($w) > 1e6} { error "not a number: $w" }
    return [expr {double($w)}]
}
set ::region {}
set ::top_bbox {}
set ::seen {}
proc bridge_um {internal} { return [format %.3f [expr {[magic::i2u $internal]}]] }
proc bridge_box_um {} {
    set b [box values]
    return [list [bridge_um [lindex $b 0]] [bridge_um [lindex $b 1]] [bridge_um [lindex $b 2]] [bridge_um [lindex $b 3]]]
}
proc bridge_view_um {} {
    set v [view get]
    return [list [bridge_um [lindex $v 0]] [bridge_um [lindex $v 1]] [bridge_um [lindex $v 2]] [bridge_um [lindex $v 3]]]
}
proc bridge_path_under {p root suffix} {
    if {$root eq "" || [string first ".." $p] >= 0} { error "bad path" }
    if {![string match "$root/*" $p] || ![string match "*$suffix" $p]} { error "path must be a $suffix file under $root" }
    return $p
}

proc cmd_PING {args} { return "pong bridge=1" }

proc cmd_LOAD {gds top} {
    bridge_path_under $gds $::bridge_repo .gds
    if {![file isfile $gds]} { error "no such file" }
    gds read $gds
    load $top
    select top cell
    set bb [bridge_box_um]
    select clear
    expand
    view
    set ::top_bbox $bb
    set ::region $bb
    set ::seen {}
    return "loaded top=$top bbox_um=$bb"
}
proc cmd_VIEW {x1 y1 x2 y2} {
    set a [list [bridge_num $x1] [bridge_num $y1] [bridge_num $x2] [bridge_num $y2]]
    if {[lindex $a 2] <= [lindex $a 0] || [lindex $a 3] <= [lindex $a 1]} { error "need x1<x2 and y1<y2" }
    box values [expr {int([magic::u2i [lindex $a 0]])}] [expr {int([magic::u2i [lindex $a 1]])}] \
         [expr {int([magic::u2i [lindex $a 2]])}] [expr {int([magic::u2i [lindex $a 3]])}]
    findbox zoom
    set ::region $a
    return "view_bbox_um=$a"
}
proc cmd_FULL {} { view; set ::region $::top_bbox; return "view_bbox_um=$::region" }
proc cmd_SEE {args} {
    if {[llength $args] != 1 || ![regexp {^[A-Za-z0-9_,*]+$} [lindex $args 0]]} { error "SEE needs one comma list of layer names" }
    see no *
    see [lindex $args 0]
    set ::seen [lindex $args 0]
    return "visible=[lindex $args 0]"
}
proc cmd_DRC {} {
    drc check
    drc catchup
    set why [drc listall why]
    set cnt [drc listall count]
    set n 0
    foreach c $cnt { if {[llength $c] >= 2} { incr n [lindex $c end] } }
    return "errors=$n why=[string range $why 0 1500] counts=[string range $cnt 0 500]"
}
proc cmd_FIND {name} {
    if {![regexp {^(?:[A-Za-z0-9_.<>/-]|\[|\]){1,100}$} $name]} { error "bad label name" }
    set before [bridge_box_um]
    findlabel $name
    set after [bridge_box_um]
    set ::region [list [expr {[lindex $after 0] - 25}] [expr {[lindex $after 1] - 25}] [expr {[lindex $after 2] + 25}] [expr {[lindex $after 3] + 25}]]
    return "box_um=$after moved=[expr {$before ne $after}]"
}
proc cmd_MEASURE {x1 y1 x2 y2} {
    set a [list [bridge_num $x1] [bridge_num $y1] [bridge_num $x2] [bridge_num $y2]]
    set u [list]
    foreach v $a { lappend u [expr {int([magic::u2i $v])}] }
    box values [lindex [lsort -integer [list [lindex $u 0] [lindex $u 2]]] 0] [lindex [lsort -integer [list [lindex $u 1] [lindex $u 3]]] 0] \
               [lindex [lsort -integer [list [lindex $u 0] [lindex $u 2]]] 1] [lindex [lsort -integer [list [lindex $u 1] [lindex $u 3]]] 1]
    set dx [expr {[lindex $a 2] - [lindex $a 0]}]
    set dy [expr {[lindex $a 3] - [lindex $a 1]}]
    return [format "dx_um=%.4f dy_um=%.4f distance_um=%.4f box_um=%s" $dx $dy [expr {hypot($dx,$dy)}] [bridge_box_um]]
}
proc cmd_PLOT {path args} {
    bridge_path_under $path "$::bridge_repo/build/agent" .pnm
    set w 1000
    if {[llength $args] > 0} { set w [expr {int([bridge_num [lindex $args 0]])}] }
    if {$w < 200 || $w > 3000} { error "width 200..3000" }
    # `plot pnm` draws what is under the box, so the box is set to the region of the last VIEW/FULL/FIND.
    # (Magic's window size is misreported without a console, so `view get` is not usable; the region is tracked here.)
    box values [expr {int([magic::u2i [lindex $::region 0]])}] [expr {int([magic::u2i [lindex $::region 1]])}] \
        [expr {int([magic::u2i [lindex $::region 2]])}] [expr {int([magic::u2i [lindex $::region 3]])}]
    if {$::seen ne ""} { plot pnm $path $w $::seen } else { plot pnm $path $w }
    return "plotted $path"
}
proc cmd_STATE {} {
    return "top=[cellname list top] view_bbox_um=$::region box_um=[bridge_box_um]"
}
proc cmd_QUIT {} {
    after 200 {quit -noprompt}
    return "quitting"
}

proc bridge_handle {line} {
    set line [string trim $line]
    if {[string length $line] > 4000} { return "ERR line too long" }
    set words [split $line " "]
    set words [lsearch -all -inline -not -exact $words ""]
    if {$::bridge_token ne ""} {
        if {[lindex $words 0] ne $::bridge_token} { return "ERR bad token" }
        set words [lrange $words 1 end]
    }
    if {[llength $words] == 0} { return "ERR empty request" }
    foreach w $words { if {![bridge_word_ok $w]} { return "ERR bad character in a word" } }
    set cmd [string toupper [lindex $words 0]]
    if {[lsearch -exact $::bridge_allowed $cmd] < 0} {
        return "ERR command not allowed: $cmd (allowed: $::bridge_allowed)"
    }
    if {[catch {set r [cmd_$cmd {*}[lrange $words 1 end]]} err]} { return "ERR $err" }
    return "OK $r"
}
proc bridge_read {ch} {
    if {[eof $ch] || [catch {gets $ch line} n] || $n < 0} { catch {close $ch}; return }
    if {[catch {bridge_handle $line} reply]} { set reply "ERR internal: $reply" }
    puts $ch [string map [list "\n" "\\n" "\r" ""] $reply]
    flush $ch
}
proc bridge_accept {ch addr port} {
    fconfigure $ch -buffering line -translation lf
    fileevent $ch readable [list bridge_read $ch]
}
# Listens on all container interfaces; start_magic.sh publishes the port as 127.0.0.1:<port> only.
socket -server bridge_accept -myaddr 0.0.0.0 $::bridge_port
puts "MAGIC_BRIDGE_LISTENING $::bridge_port"
