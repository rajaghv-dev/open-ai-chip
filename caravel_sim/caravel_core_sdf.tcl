# OpenSTA: SDF for the caravel-lite CC2509 caravel_core gate netlist (flat, includes the management SoC) from the shipped nom SPEF.
set root $::env(ROOT)
set pdk  $::env(PDK_ROOT)/sky130A/libs.ref
set corner $::env(CORNER_LIB)     ;# e.g. sky130_fd_sc_hd__tt_025C_1v80
set spef_c $::env(SPEF_CORNER)    ;# nom|min|max
read_liberty $pdk/sky130_fd_sc_hd/lib/$corner.lib
read_liberty $pdk/sky130_fd_sc_hvl/lib/sky130_fd_sc_hvl__tt_025C_3v30_lv1v80.lib
set cv $root/build/caravel/caravel/verilog
foreach f {housekeeping gpio_logic_high gpio_defaults_block spare_logic_block xres_buf user_id_programming mprj_logic_high mprj2_logic_high mprj_io_buffer mgmt_protect_hv caravel_clocking caravel_core} {
  read_verilog $root/build/caravel/sta/gl_clean/$f.v
}
read_verilog $root/build/caravel/mgmt_core_wrapper/verilog/gl/RAM128.v
read_verilog $root/build/caravel/sta/simple_por_stub.v
read_verilog $root/build/caravel/sta/gl_clean/empty_macro.v
read_verilog $root/build/caravel/sta/gl_clean/manual_power_connections.v
read_verilog $root/build/caravel/sta/gl_clean/__user_project_wrapper.v
link_design caravel_core
puts "linked: [llength [get_cells -hierarchical *]] cells"
read_spef $cv/../signoff/caravel_core/openlane-signoff/spef/caravel_core.$spef_c.spef
report_checks -path_delay max -format end -group_path_count 1 -unconstrained 
write_sdf -divider . -digits 3 $env(OUT_SDF)
puts "sdf written"
exit
