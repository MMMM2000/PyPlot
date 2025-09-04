#include <Origin.h>

// run a LabTalk command
static void lt(const string& s){ LT_execute(s); }

// CSV columns: X_raw_a, Y_raw_a, X_raw_b, Y_raw_b, X_mean_a, Y_mean_a, X_mean_b, Y_mean_b
bool plot_stress_csv(string csv, string title, string xlabel, string ylabel,
                     double delta, string template_otp, string export_png)
{
    // 1) import CSV to a fresh worksheet
    Worksheet wks;
    wks.Create("Origin", CREATE_VISIBLE);
    int rc = wks.ImportASCII(csv);
    if(rc != 0) return false;
    if(wks.GetNumCols() < 8) return false;

    // (optional) mark XY designations so the sheet looks right
    for(int i=0; i<8; i+=2){
        Column cx = wks.Columns(i);
        Column cy = wks.Columns(i+1);
        cx.SetType(OKDATAOBJ_DESIGNATION_X);
        cy.SetType(OKDATAOBJ_DESIGNATION_Y);
    }

    // basic sanity: first pair must have data
    Dataset x1(wks, 0), y1(wks, 1);
    if(x1.GetSize() <= 0 || y1.GetSize() <= 0) return false;

    // 2) define ranges and plot (first call creates the graph)
    string bname = wks.GetPage().GetName();
    string sname = wks.GetName();
    string base = "[" + bname + "]" + sname + "!";
    lt("range r1=" + base + "(1,2);");
    lt("range r2=" + base + "(3,4);");
    lt("range r3=" + base + "(5,6);");
    lt("range r4=" + base + "(7,8);");

    lt("plotxy iy:=r1 plot:=202;");              // scatter
    lt("layer -i r2; layer -i r3; layer -i r4;");

    // 3) styling (safe defaults; feel free to push all this into a .otp)
    lt("set p1 -w 1; set p2 -w 1; set p3 -w 3; set p4 -w 3;");
    lt("set p1 -z 1; set p2 -z 1; set p3 -z 8; set p4 -z 8;");
    // raw points (p1, p2) use symbol color; means (p3, p4) use line color
    lt("set p1 -c rgb(230,159,0); set p3 -cl rgb(230,159,0);");  // 'a' = orange
    lt("set p2 -c rgb(86,180,233); set p4 -cl rgb(86,180,233);");  // 'b' = blue
    // refresh legend with sample name and color-matched entries
    LT_set_str("sname$", sname);
    lt("legend.text$=sname$+\"\\n\\l(1) raw a\\n\\l(2) raw b\\n\\l(3) mean a\\n\\l(4) mean b\";");

    // 4) titles & rescale
    LT_set_str("ttl$", title);
    LT_set_str("xl$",  xlabel);
    LT_set_str("yl$",  ylabel);
    lt("title -s ttl$;");
    lt("layer.x.title$=xl$; layer.y.title$=yl$;");
    lt("layer -a;");   // rescale

    // 5) optional export
    if(!export_png.IsEmpty()){
        LT_set_str("_exp$", export_png);
        lt("expGraph type:=png tr1:=300 filename:=_exp$;");
    }
    return true;
}
