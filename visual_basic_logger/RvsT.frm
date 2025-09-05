VERSION 5.00
Object = "{648A5603-2C6E-101B-82B6-000000000014}#1.1#0"; "mscomm32.ocx"
Begin VB.Form Form1 
   Caption         =   "Form1"
   ClientHeight    =   3795
   ClientLeft      =   60
   ClientTop       =   405
   ClientWidth     =   7515
   LinkTopic       =   "Form1"
   ScaleHeight     =   3795
   ScaleWidth      =   7515
   StartUpPosition =   3  'Windows Default
   Begin VB.Frame Repeat 
      Caption         =   "Repeat"
      Height          =   495
      Left            =   120
      TabIndex        =   21
      Top             =   840
      Width           =   1215
      Begin VB.CheckBox Check1 
         Caption         =   "Check1"
         Height          =   255
         Left            =   840
         TabIndex        =   22
         Top             =   120
         Width           =   255
      End
   End
   Begin VB.Frame Frame7 
      Caption         =   "servis"
      Height          =   1815
      Left            =   120
      TabIndex        =   18
      Top             =   1680
      Width           =   1215
      Begin VB.TextBox Text6 
         Height          =   285
         Left            =   120
         TabIndex        =   20
         Text            =   "Text6"
         Top             =   600
         Width           =   975
      End
      Begin VB.TextBox Text7 
         Height          =   285
         Left            =   120
         TabIndex        =   19
         Text            =   "Text7"
         Top             =   1320
         Width           =   975
      End
   End
   Begin VB.Frame Frame5 
      Caption         =   "Sample ID"
      Height          =   615
      Left            =   1680
      TabIndex        =   13
      Top             =   2880
      Width           =   5535
      Begin VB.TextBox Text8 
         Height          =   285
         Left            =   240
         TabIndex        =   15
         Text            =   "ID"
         Top             =   240
         Width           =   5175
      End
   End
   Begin VB.Frame Frame4 
      Caption         =   "Save data to"
      Height          =   615
      Left            =   1680
      TabIndex        =   12
      Top             =   2160
      Width           =   5535
      Begin VB.TextBox Text3 
         Height          =   285
         Left            =   240
         TabIndex        =   14
         Text            =   "c:\Rasto\data\data.dat"
         Top             =   240
         Width           =   5175
      End
   End
   Begin VB.Timer Timer1 
      Interval        =   100
      Left            =   360
      Top             =   1560
   End
   Begin VB.Frame HMP4030 
      Caption         =   "HMP4030"
      Height          =   1815
      Left            =   1680
      TabIndex        =   1
      Top             =   240
      Width           =   5535
      Begin VB.Frame Frame6 
         Caption         =   "Iset (A)"
         Height          =   615
         Left            =   3720
         TabIndex        =   16
         Top             =   960
         Width           =   1695
         Begin VB.TextBox Text4 
            Height          =   285
            Left            =   240
            TabIndex        =   17
            Text            =   "0"
            Top             =   240
            Width           =   1215
         End
      End
      Begin VB.Frame Frame3 
         Caption         =   "I (A) Ch3"
         Height          =   615
         Left            =   1920
         TabIndex        =   9
         Top             =   960
         Width           =   1695
         Begin VB.TextBox Text5 
            Height          =   285
            Left            =   240
            TabIndex        =   11
            Text            =   "????"
            Top             =   240
            Width           =   1215
         End
      End
      Begin VB.Frame Frame2 
         Caption         =   "U (V) Ch3"
         Height          =   615
         Left            =   120
         TabIndex        =   8
         Top             =   960
         Width           =   1695
         Begin VB.TextBox Text2 
            Height          =   285
            Left            =   240
            TabIndex        =   10
            Text            =   "????"
            Top             =   240
            Width           =   1215
         End
      End
      Begin VB.Frame Frame1 
         Caption         =   "step (mA)"
         Height          =   615
         Index           =   2
         Left            =   3720
         TabIndex        =   6
         Top             =   240
         Width           =   1695
         Begin VB.TextBox Text1 
            Height          =   285
            Index           =   2
            Left            =   240
            TabIndex        =   7
            Text            =   "1"
            Top             =   240
            Width           =   1215
         End
      End
      Begin VB.Frame Frame1 
         Caption         =   "to (mA)"
         Height          =   615
         Index           =   1
         Left            =   1920
         TabIndex        =   4
         Top             =   240
         Width           =   1695
         Begin VB.TextBox Text1 
            Height          =   285
            Index           =   1
            Left            =   240
            TabIndex        =   5
            Text            =   "100"
            Top             =   240
            Width           =   1215
         End
      End
      Begin VB.Frame Frame1 
         Caption         =   "from (mA)"
         Height          =   615
         Index           =   0
         Left            =   120
         TabIndex        =   2
         Top             =   240
         Width           =   1695
         Begin VB.TextBox Text1 
            Height          =   285
            Index           =   0
            Left            =   240
            TabIndex        =   3
            Text            =   "1"
            Top             =   240
            Width           =   1215
         End
      End
   End
   Begin VB.CommandButton Command1 
      Caption         =   "Start R vs. I"
      Height          =   495
      Left            =   120
      TabIndex        =   0
      Top             =   240
      Width           =   1215
   End
   Begin MSCommLib.MSComm MSComm1 
      Left            =   240
      Top             =   840
      _ExtentX        =   1005
      _ExtentY        =   1005
      _Version        =   393216
      DTREnable       =   0   'False
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Dim sec As Integer

Private Sub Command1_Click()

Open Text3.Text For Append As #1
Print #1, Text8.Text
Print #1, "Iset(mA)", "Ireal (mA)", "Ureal (mA)", "R(ohm)"
Close #1


If MSComm1.PortOpen = False Then MSComm1.PortOpen = True
MSComm1.Output = "INST OUT3" + Chr(10)
MSComm1.Output = "VOLT 32" + Chr(10)
MSComm1.Output = "CURR 0" + Chr(10)
MSComm1.Output = "OUTP ON" + Chr(10)

Do

 For i = Val(Text1(0).Text) To Val(Text1(1).Text) Step Val(Text1(2).Text)
  MSComm1.Output = "CURR" + Str(i / 1000) + Chr(10)
 sec = 0
 Timer1.Enabled = True
  While sec < 10  'caka 1 s
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False

 Text6.Text = MSComm1.Input
 MSComm1.Output = "MEAS:VOLT?" + Chr(10)
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False
 Text2.Text = MSComm1.Input
  
  
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False

 Text6.Text = MSComm1.Input
 MSComm1.Output = "MEAS:CURR?" + Chr(10)
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False
 Text5.Text = MSComm1.Input

 Text4.Text = i / 1000

 Open Text3.Text For Append As #1
 Print #1, Val(Text4.Text), Val(Text5.Text), Val(Text2.Text), Val(Text2.Text) / Val(Text5.Text) ' zapise do suboru
 Close #1
  

 Next i

 For i = Val(Text1(1).Text) To Val(Text1(0).Text) Step -1 * Val(Text1(2).Text)
  MSComm1.Output = "CURR" + Str(i / 1000) + Chr(10)
 sec = 0
 Timer1.Enabled = True
   While sec < 10  'caka 1 s
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False

 Text6.Text = MSComm1.Input
 MSComm1.Output = "MEAS:VOLT?" + Chr(10)
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False
 Text2.Text = MSComm1.Input
  
  
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False

 Text6.Text = MSComm1.Input
 MSComm1.Output = "MEAS:CURR?" + Chr(10)
 sec = 0
 Timer1.Enabled = True
  While sec < 1  'caka 100 ms
   Text7.Text = sec
   DoEvents
  Wend
 Timer1.Enabled = False
 Text5.Text = MSComm1.Input

 Text4.Text = i / 1000

 Open Text3.Text For Append As #1
 Print #1, Val(Text4.Text), Val(Text5.Text), Val(Text2.Text), Val(Text2.Text) / Val(Text5.Text) ' zapise do suboru
 Close #1
  

 Next i

Loop While Check1.Value = 1


Text4.Text = 0
MSComm1.Output = "VOLT 0" + Chr(10)
MSComm1.Output = "CURR 0" + Chr(10)
MSComm1.PortOpen = False

'If MSComm2.PortOpen = False Then MSComm2.PortOpen = True

'MSComm2.Output = Text2.Text + Chr(13)
'Text3 = MSComm2.Input
'MSComm2.PortOpen = False
'Text2.Text = "Hotovo"
End Sub

Private Sub Timer1_Timer()
    sec = sec + 1
End Sub
