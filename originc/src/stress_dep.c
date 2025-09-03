// originc/src/stress_dep.c
#include <Origin.h>

// Helper to run LabTalk
static void lt(const string& s) { LT_execute(s); }
// Helpers to pass values safely into LabTalk
static void lt_set_str(const string& name, const string& val) { LT_set_str(name, val); }
static void lt_set_var(const string& name, double v) { LT_set_var(name, v); }

// CSV schema (8 columns):
// X_raw_a, Y_raw_a, X_raw_b, Y_raw_b, X_mean_a, Y_mean_a, X_mean_b, Y_mean_b
//
// Arguments:
//  csv           : path to CSV (see schema above)
//  title         : graph title
//  xlabel        : X axis title
//  ylabel        : Y axis title
//  delta         : numeric Δ to annotate (µs)
//  template_otp  : optional path to a .otp template ("" to skip)
//  export_png    : optional path to export PNG ("" to skip)
bool plot_stress_csv(string csv, string title, string xlabel, string ylabel,
                     double delta, string template_otp, string export_png)
{
    // 1) Import CSV into a fresh worksheet
    Worksheet wks; 
    wks.Create("Origin", CREATE_VISIBLE);
    int rc = wks.ImportASCII(csv);
    if (rc != 0)
        return false;

    // Ensure XY designations for the 4 pairs
    for(int i = 0; i < 8; i += 2) {
        wks.Columns(i).Type   = OKDATAOBJ_DESIGNATION_X;
        wks.Columns(i+1).Type = OKDATAOBJ_DESIGNATION_Y;
    }

    // 2) Create graph (apply template if provided)
    GraphPage gp;
    if (template_otp.IsEmpty())
        gp.Create("Origin", CREATE_VISIBLE);
    else
        gp.Create("Origin", CREATE_VISIBLE, template_otp);

    GraphLayer gl = gp.Layers();

    // Build LabTalk ranges to add plots
    string bname = wks.GetPage().GetName();
    string sname = wks.GetName();
    string base = "[" + bname + "]" + sname + "!";
    lt("range r1=" + base + "(1,2);"); // raw a
    lt("range r2=" + base + "(3,4);"); // raw b
    lt("range r3=" + base + "(5,6);"); // mean a
    lt("range r4=" + base + "(7,8);"); // mean b

    // Add four plots
    lt("layer -i r1; layer -i r2; layer -i r3; layer -i r4;");

    // Force types: 202=scatter, 200=line+symbol (common internal codes)
    lt("set p1 -c 202; set p2 -c 202; set p3 -c 200; set p4 -c 200;");

    // Styling (adjust later via template if desired)
    lt("set p1 -k 1; set p2 -k 1; set p3 -k 1; set p4 -k 1;");   // circle symbols
    lt("set p1 -w 1; set p2 -w 1; set p3 -w 3; set p4 -w 3;");   // line widths
    lt("set p1 -z 1; set p2 -z 1; set p3 -z 8; set p4 -z 8;");   // symbol sizes
    // Optional palette colors (can be overridden by template)
    lt("set p1 -cl 0xE69F00; set p3 -cl 0xE69F00;"); // 'a' series
    lt("set p2 -cl 0x56B4E9; set p4 -cl 0x56B4E9;"); // 'b' series

    // 3) Labels, title, Δ box
    lt_set_str("ttl$", title);
    lt_set_str("xl$", xlabel);
    lt_set_str("yl$", ylabel);
    lt_set_var("dlt", delta);

    lt("title -s ttl$;");
    lt("layer.x.title$=xl$; layer.y.title$=yl$;");
    lt("layer -a;");           // rescale
    lt("layer.grid=1;");       // grid

    // Annotate Δ at top-right (relative coords). Tweak offsets as needed.
    lt("text -s 95 5 \"Δ=\"+$(dlt,.2)+\" µs\";");

    // 4) Optional export
    if (!export_png.IsEmpty()) {
        lt_set_str("_exp$", export_png);
        lt("expGraph type:=png tr1:=300 filename:=_exp$;");
    }

    return true;
}

