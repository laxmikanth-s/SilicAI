import os
import sys
import re
import importlib.util
from pathlib import Path
from typing import Tuple, Dict


def _load_wrapper_class(module_path: Path, class_name: str):
    spec = importlib.util.spec_from_file_location(module_path.stem, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    if not hasattr(module, class_name):
        raise ImportError(f"Class {class_name} not found in {module_path}")
    return getattr(module, class_name)


def generate_verilog_from_prompt(prompt: str) -> Tuple[str, str]:
    """Very simple rule-based RTL generator. Returns (module_name, verilog_text)."""
    p = prompt.lower().strip()

    # 2:1 mux
    mux_match = re.search(r"(\d+)\s*bit\s*mux", p)
    if mux_match:
        width = int(mux_match.group(1))
        module_name = f"mux{width}_2to1"
        verilog = []
        verilog.append(f"module {module_name}(")
        verilog.append(f"    input  [{width-1}:0] a,")
        verilog.append(f"    input  [{width-1}:0] b,")
        verilog.append(f"    input                sel,")
        verilog.append(f"    output [{width-1}:0] y");
        verilog.append(");")
        verilog.append(f"    assign y = sel ? b : a;")
        verilog.append("endmodule")
        return module_name, "\n".join(verilog)

    # default: passthrough
    module_name = "passthrough"
    verilog = [
        "module passthrough(",
        "    input  [1:0] a,",
        "    output [1:0] y",
        ");",
        "    assign y = a;",
        "endmodule",
    ]
    return module_name, "\n".join(verilog)


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def run_flow(prompt: str,
             work_dir: Path,
             tech: Dict[str, str]) -> Dict[str, str]:
    """Generate RTL, synthesize with Yosys, and run OpenROAD basic flow.

    tech expects keys: tech_lef, lib_lef, liberty, sdc (optional)
    """
    ensure_dir(work_dir)
    rtl_dir = work_dir / "rtl"
    out_dir = work_dir / "out"
    ensure_dir(rtl_dir)
    ensure_dir(out_dir)

    module_name, verilog_text = generate_verilog_from_prompt(prompt)
    rtl_file = rtl_dir / f"{module_name}.v"
    rtl_file.write_text(verilog_text, encoding="utf-8")

    synthesized_netlist = out_dir / f"{module_name}_synth.v"

    # Dynamically import wrappers from 'Wrapper Classes'
    wrapper_dir = Path(__file__).resolve().parent / 'Wrapper Classes'
    yosys_path = wrapper_dir / 'yosys_wrapper.py'
    openroad_path = wrapper_dir / 'OpenROAD_wrapper.py'

    YosysWrapper = _load_wrapper_class(yosys_path, 'YosysWrapper')
    OpenROADGUIWrapper = _load_wrapper_class(openroad_path, 'OpenROADGUIWrapper')

    with YosysWrapper(debug_mode=True) as yosys:
        yosys_result = yosys.synthesize_design(
            verilog_files=[str(rtl_file)],
            top_module=module_name,
            output_file=str(synthesized_netlist),
            target="generic",
            defines=None,
            show_statistics=True,
        )

    if not yosys_result.success:
        return {"status": "synthesis_failed"}

    # OpenROAD section
    openroad = OpenROADGUIWrapper()
    if not openroad.openroad_path:
        return {"status": "openroad_not_found"}

    tcl_file = out_dir / f"{module_name}_flow.tcl"
    openroad.write_basic_flow_tcl(
        tcl_path=str(tcl_file),
        design_name=module_name,
        verilog_path=str(synthesized_netlist),
        top_module=module_name,
        tech_lef_path=tech["tech_lef"],
        lib_lef_path=tech["lib_lef"],
        liberty_path=tech["liberty"],
        sdc_path=tech.get("sdc"),
    )

    stdout = openroad.run_script_terminal(str(tcl_file))
    (out_dir / "openroad.log").write_text(stdout or "", encoding="utf-8")

    return {
        "status": "ok",
        "module": module_name,
        "rtl": str(rtl_file),
        "netlist": str(synthesized_netlist),
        "tcl": str(tcl_file),
        "log": str(out_dir / "openroad.log"),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m SilicAI.agent "
              "\"create a 2 bit mux\" [WORK_DIR] [TECH_ROOT]")
        sys.exit(1)

    prompt = sys.argv[1]
    work_dir = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("./work")

    # Simple tech config via environment or defaults
    tech_root = Path(sys.argv[3]) if len(sys.argv) >= 4 else Path(os.getenv("OPENROAD_TECH", "D:/OpenROAD/flow/tech/nangate45"))
    tech = {
        "tech_lef": str(tech_root / "Nangate45.tech.lef"),
        "lib_lef": str(tech_root / "Nangate45.macro.lef"),
        "liberty": str(tech_root / "Nangate45_typ.lib"),
        "sdc": None,
    }

    results = run_flow(prompt, work_dir, tech)
    print(results)


if __name__ == "__main__":
    main()


