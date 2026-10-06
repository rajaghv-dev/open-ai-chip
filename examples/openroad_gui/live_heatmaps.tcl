# live_heatmaps.tcl -- interactive OpenROAD GUI (XQuartz / X11): load a finished run, build the engine heat maps and
# cycle through them in the open window. Started by: bash scripts/gui/open_gui.sh heatmaps <design>
# Env: ODB, LIB, SDC, SPEF (paths inside the container), PWR (default vccd1), VOLT (1.8), DWELL (seconds per view, 12).
# Read only: nothing is written back to the run (the IR-drop voltage file goes to /tmp in the container).
proc env_or {k d} { if {[info exists ::env($k)]} { return $::env($k) } else { return $d } }

read_db $::env(ODB)
read_liberty $::env(LIB)
read_sdc $::env(SDC)
read_spef $::env(SPEF)
set pwr  [env_or PWR vccd1]
set volt [env_or VOLT 1.8]
set ::DWELL [expr {int([env_or DWELL 12]) * 1000}]

# psm needs its analysis before the IR Drop map has data (as librelane's irdrop.tcl does)
if {[catch {
  set_pdnsim_net_voltage -net $pwr -voltage $volt
  analyze_power_grid -net $pwr -voltage_file /tmp/ir_$pwr.csv
  gui::set_heatmap IRDrop Net $pwr
  gui::set_heatmap IRDrop Layer met1
} e]} { puts "IR drop analysis skipped: $e" }

# name shown, display control, heat-map name (empty = plain layout)
set ::VIEWS {
  {"layout (all layers)"                          ""                             ""}
  {"gpl: placement density"                       "Heat Maps/Placement Density"  Placement}
  {"grt: routing congestion"                      "Heat Maps/Routing Congestion" Routing}
  {"power density (from liberty + SPEF activity)" "Heat Maps/Power Density"      Power}
  {"psm: IR drop on met1"                         "Heat Maps/IR Drop"            IRDrop}
}
foreach v $::VIEWS { if {[lindex $v 2] ne ""} { catch {gui::set_heatmap [lindex $v 2] rebuild} } }

proc show_view {i} {
  set v [lindex $::VIEWS $i]
  catch {gui::set_display_controls "Heat Maps/*" visible false}
  if {[lindex $v 1] eq ""} {
    gui::set_display_controls "Layers/*" visible true
    gui::set_display_controls "Instances/*" visible true
  } else {
    # hide the layers so the colour map reads clearly
    gui::set_display_controls "Layers/*" visible false
    gui::set_display_controls "Instances/*" visible true
    gui::set_display_controls [lindex $v 1] visible true
  }
  catch {gui::fit}
  puts "HEATMAP_VIEW [expr {$i + 1}]/[llength $::VIEWS]: [lindex $v 0]"
}
puts "OPENROAD_GUI_LOADED [[ord::get_db_block] getName] (views change every [expr {$::DWELL / 1000}] s; close the window to stop)"
# OpenROAD runs Qt's event loop, not Tcl's, so `after` timers never fire; gui::pause waits while the window stays live.
# Cycles ROUNDS times (default 3), then leaves the plain layout up for interactive use.
set rounds [env_or ROUNDS 3]
for {set r 0} {$r < $rounds} {incr r} {
  for {set i 0} {$i < [llength $::VIEWS]} {incr i} {
    show_view $i
    gui::pause $::DWELL
  }
}
show_view 0
puts "HEATMAP_DONE: interactive now (View menu > Heat Maps to switch by hand)"
