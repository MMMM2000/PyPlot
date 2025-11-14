### MPa in stress dependence/sensitivity:  
-since different dimensions mean different different stress with the same load, it would be better to start using stress instead of load. When stress dependence is selected in data logger, there could be an option to input d and D of the microwire, and it could automatically calculate loads necessary for 20, 40, 60, 80 and 100 MPa. We could then use MPa values in stress dependendce and stress sensitivity pyplot plugins. Here is a sample way to calculate the stress in microwires:  
geometry  
d_m = 26e-6     # metal core diameter [m]  
d_gl = 60e-6    # total diameter [m]  
r_m = d_m / 2  
r_gl = d_gl / 2  

S_m = math.pi * r_m**2  
S_gl = math.pi * (r_gl**2 - r_m**2)  

moduli  
E_m = 120e9     # Pa  
E_gl = 70e9     # Pa  
K = E_m / E_gl  

g = 9.81        # m/s²  

def stress_metal_core(m_grams):  
    m = m_grams / 1000      # kg  
    P = m * g               # N  
    sigma = (K * P) / (K*S_m + S_gl)  
    return sigma / 1e6      # MPa  

### Increasing force/strain different color that decreasing:  
-for pdf plotter

### Better workbook functionality:  
-Plot buttons now regenerate workbooks automatically for each graph (1 workbook per graph) with working "long name", "units", "comments", "notes" and "F(x)" rows. Keep pushing on column/row editing so values remain easy to tweak.

### simple plotting scripts:  
-maybe have some folder for simple scripts, that will have the functionality of pyplot plugins, but just open s simple tkinter gui. Basically, much simpler and easier to maintain. Just open, import files/folders and open matplotlib or origin graphs. Or there could be some toggle in the plotting section in the launcher, where I could toggle between pyplot and simple scripts. 

### save on close:  
-ask to save on close in pyplot and data builder (already works in pyplot)

### trhačka MPa a %:  
-ability to calculate MPa from force in pdf plotter graphs

### Export data for origin:  
-export well formated data that I can just import to origin. Or open workbooks in origin and continue from there.

### Veusz:  
-check its repo to see if we can reuse anything or get inspired

### Undo:  
-full undo/redo functionality, with undo/redo history showing exact actions

### Import opju:  
-ability to import .opju files in pyplot, but I am not sure if this is all that useful

### More VSM info:  
-extract more info about the experiment from vsm data files

### Database builder update:  
-keep working on it untill everything works

### Gnu plot:  
-same as with veusz

### PyLab:  
-I think I was considering renaming either the data builder or the logger, but I do not remember

### Icon:  
-pyplot already has an icon, but maybe using slightly different icons for different windows?

### legend:  
-more legend options, and fix some that are currently not working in pyplot

### Check outliers:  
-make this button functional in pyplot

### Export workbooks to origin:  
-this button already exists, but I did not yet check if it works

### Version history:  
-not just undo/redo, but being able to see all the changes I have made and go back to them