#include <Origin.h>

// Run a LabTalk command
static void lt(const string& s){ LT_execute(s); }

// csv has 8 columns:
// X_raw_a, Y_raw_a, X_raw_b, Y_raw_b, X_mean_a, Y_mean_a, X_mean_b, Y_mean_b
bool plot_stress_csv(string csv, string title, string xlabel, string ylabel,
                     double delta, string template_otp, string export_png)
{
    // 1) Import CSV into a fresh worksheet
    Worksheet wks;
    wks.Create("Origin", CREATE_VISIBLE);

    // Use Import ASCII; if it fails, bail early
    int rc = wks.ImportASCII(csv);
    if(rc != 0)
        return false;

    // Ensure we got at least 8 columns
    if(wks.GetNumCols() < 8)
        return false;

    // Force numeric content (handles locale issues, e.g., commas/periods)
    wks.Activate();
    for(int c = 1; c <= 8; c++){
        string cmd;
        cmd.Format("wcol(%d)=value(wcol(%d));", c, c);
        lt(cmd);
    }

    // Optional: mark XY designations so sheet looks correct
    for (int i = 0; i < 8; i += 2) {
        Column cx = wks.Columns(i);
        Column cy = wks.Columns(i + 1);
        cx.SetType(OKDATAOBJ_DESIGNATION_X);
        cy.SetType(OKDATAOBJ_DESIGNATION_Y);
    }

    // Build LabTalk ranges for the 4 XY pairs
    string bname = wks.GetPage().GetName();
    string sname = wks.GetName();
    string base = "[" + bname + "]" + sname + "!";
    lt("range r1=" + base + "(1,2);"); // raw a
    lt("range r2=" + base + "(3,4);"); // raw b
    lt("range r3=" + base + "(5,6);"); // mean a
    lt("range r4=" + base + "(7,8);"); // mean b

    // 2) Create graph: first series makes a new graph, others append to same layer
    lt("plotxy iy:=r1 plot:=202;");        // scatter
    lt("layer -i r2; layer -i r3; layer -i r4;");

    // 3) Styling (safe defaults; you can move to .otp later)
    lt("set p1 -c 202; set p2 -c 202; set p3 -c 200; set p4 -c 200;"); // types
    lt("set p1 -k 1; set p2 -k 1; set p3 -k 1; set p4 -k 1;");         // circle symbols
    lt("set p1 -w 1; set p2 -w 1; set p3 -w 3; set p4 -w 3;");         // line widths
    lt("set p1 -z 1; set p2 -z 1; set p3 -z 8; set p4 -z 8;");         // symbol sizes
    // BGR colors; override via template if needed
    lt("set p1 -cl 0xE69F00; set p3 -cl 0xE69F00;");                   // 'a' series
    lt("set p2 -cl 0x56B4E9; set p4 -cl 0x56B4E9;");                   // 'b' series

    // 4) Labels & rescale
    LT_set_str("ttl$", title);
    LT_set_str("xl$", xlabel);
    LT_set_str("yl$", ylabel);
    lt("title -s ttl$;");
    lt("layer.x.title$=xl$; layer.y.title$=yl$;");
    lt("layer -a;");    // rescale

    // 5) Optional export
    if (!export_png.IsEmpty()) {
        LT_set_str("_exp$", export_png);
        lt("expGraph type:=png tr1:=300 filename:=_exp$;");
    }
    return true;
}
