# views.tcl - render OpenROAD GUI views off-screen from a finished LibreLane run.
# Run through render_views.sh (sets the env below). Every view is wrapped in catch, so one
# failing view does not stop the rest; results are printed as "VIEW <name> OK|FAIL ...".
#   env: ODB LIB SDC SPEF OUT TOP CLK WIDTH PWR GND VOLT
# Docs: examples/openroad_gui/README.md, docs/OPENROAD_ENGINES.md, docs/GUI_AND_LOGS.md
proc env_or {k d} { if {[info exists ::env($k)]} { return $::env($k) } else { return $d } }
set out   $::env(OUT)
set W     [env_or WIDTH 1100]
set clk   [env_or CLK clk]
set pwr   [env_or PWR vccd1]
set gnd   [env_or GND vssd1]
set volt  [env_or VOLT 1.8]

proc shot {name} {
  global out W
  set f $out/$name.png
  file delete -force $f
  # -area is mandatory off-screen: gui::fit has no visible viewer to fit, and a plain save_image
  # then fails with GUI-0078. The die area in microns (plus a 2 um margin) fixes the view.
  catch {save_image -area $::AREA -width $W $f} e
  if {[file exists $f] && [file size $f] > 1000} { puts "VIEW $name OK [file size $f] bytes" } else { puts "VIEW $name FAIL no image ($e)" }
}
# Show one heat map on a quiet background (layers, instances and nets hidden so the colours read).
proc heat_on {ctrl} {
  gui::set_display_controls "Layers/*" visible false
  gui::set_display_controls "Instances/*" visible false
  gui::set_display_controls "Nets/*" visible false
  gui::set_display_controls $ctrl visible true
}
# Declutter for highlight views: hide power/ground nets, filler and tap cells; highlights stay.
proc quiet_on {} {
  foreach c {"Nets/Power" "Nets/Ground" "Instances/Physical/*"} { catch {gui::set_display_controls $c visible false} }
}
proc quiet_off {} {
  foreach c {"Nets/Power" "Nets/Ground" "Instances/Physical/*"} { catch {gui::set_display_controls $c visible true} }
}
proc heat_off {} {
  gui::set_display_controls "Heat Maps/*" visible false
  gui::set_display_controls "Layers/*" visible true
  gui::set_display_controls "Instances/*" visible true
  gui::set_display_controls "Nets/*" visible true
}
proc view {name body} { if {[catch {uplevel 1 $body} e]} { puts "VIEW $name FAIL $e" } }

if {[catch {
  read_db $::env(ODB)
  read_liberty $::env(LIB)
  read_sdc $::env(SDC)
  read_spef $::env(SPEF)
} e]} { puts "VIEW setup FAIL $e"; exit 1 }
set blk [ord::get_db_block]
set dbu [$blk getDbUnitsPerMicron]
set die [$blk getDieArea]
set AREA [list [expr {[$die xMin]/double($dbu) - 2}] [expr {[$die yMin]/double($dbu) - 2}] \
               [expr {[$die xMax]/double($dbu) + 2}] [expr {[$die yMax]/double($dbu) + 2}]]
puts "design: [[ord::get_db_block] getName]"

# 1. full layout (all layers as stored in the final ODB: cells, power grid, routing)
view 01_layout { shot 01_layout }

# 2. gpl: placement density heatmap
view 02_placement_density {
  gui::set_heatmap Placement rebuild
  heat_on "Heat Maps/Placement Density"
  shot 02_placement_density
  heat_off
}

# 3. grt/RUDY: congestion heatmaps (RUDY = estimate from the placed netlist; Routing = global router usage)
# RUDY: gui::set_heatmap RUDY exists, but this build registers no "Heat Maps/RUDY" display control,
# so it cannot be switched on from a script (verified: GUI-0013). The Routing heat map below is used instead.
view 03_routing_congestion {
  gui::set_heatmap Routing rebuild
  heat_on "Heat Maps/Routing Congestion"
  shot 03_routing_congestion
  heat_off
}

# 4. power density (needs liberty; static estimate from cell power)
view 04_power_density {
  gui::set_heatmap Power rebuild
  heat_on "Heat Maps/Power Density"
  shot 04_power_density
  heat_off
}

# 5. psm: IR drop (analyze_power_grid, as in librelane's irdrop.tcl)
view 05_ir_drop {
  set_pdnsim_net_voltage -net $pwr -voltage $volt
  analyze_power_grid -net $pwr -voltage_file $out/ir_$pwr.csv
  gui::set_heatmap IRDrop Net $pwr
  # default Layer is li1 (no data); met1 carries the standard-cell rails, which is where the drop is
  gui::set_heatmap IRDrop Layer met1
  gui::set_heatmap IRDrop rebuild
  heat_on "Heat Maps/IR Drop"
  shot 05_ir_drop
  heat_off
}

# 6. cts: clock tree. (a) clock nets/sinks highlighted on the layout, (b) the clock tree viewer image
view 06_clock_tree_highlight {
  set n 0; set k 0
  foreach net [$blk getNets] {
    if {[$net getSigType] eq "CLOCK" && ![string match "*vccd*" [$net getName]]} {
      gui::highlight_net [$net getName] 0; incr n
      foreach it [$net getITerms] { if {[$it isInputSignal]} { gui::highlight_inst [[$it getInst] getName] 0; incr k } }
    }
  }
  puts "clock nets highlighted: $n, clock sink/buffer pins: $k"
  quiet_on
  shot 06_clock_tree_highlight
  quiet_off
  gui::clear_highlights 0
}
view 06_clock_tree_viewer {
  set f $out/06_clock_tree_viewer.png
  file delete -force $f
  save_clocktree_image -clock $clk -width 1000 -height 700 $f
  if {[file exists $f]} { puts "VIEW 06_clock_tree_viewer OK [file size $f] bytes" } else { puts "VIEW 06_clock_tree_viewer FAIL no image" }
}

# 7. sta: worst setup path highlighted (every instance and net on the path)
view 07_worst_setup_path {
  set pe [lindex [find_timing_paths -path_delay max -sort_by_slack -group_path_count 1] 0]
  puts "worst setup slack (ns): [get_property $pe slack]"
  set path [$pe path]
  set i 0
  foreach p [$path pins] {
    set pn [get_full_name $p]
    set it [$blk findITerm $pn]
    if {$it eq "NULL"} { continue }
    gui::highlight_inst [[$it getInst] getName] 1
    set nt [$it getNet]
    if {$nt ne "NULL"} { gui::highlight_net [$nt getName] 2 }
    incr i
  }
  puts "path pins highlighted: $i"
  quiet_on
  shot 07_worst_setup_path
  quiet_off
  gui::clear_highlights 1
  gui::clear_highlights 2
}
exit
