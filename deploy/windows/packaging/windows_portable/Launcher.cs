using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Collections.Generic;

internal sealed class AICareLauncher : Form {
    readonly string root, edition, home;
    readonly Label status;
    Process backend;
    StreamWriter log;
    readonly object logLock = new object();
    string token, url, readyFile;
    bool closing = false;
    public AICareLauncher() {
        root = AppDomain.CurrentDomain.BaseDirectory;
        edition = File.ReadAllText(Path.Combine(root,"EDITION.txt")).Trim();
        home = Environment.GetEnvironmentVariable("AI_CARE_DATA_HOME");
        if (String.IsNullOrEmpty(home)) home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AICareDemo",edition);
        Directory.CreateDirectory(Path.Combine(home,"logs"));
        Text = "AI Care — " + edition; ClientSize = new Size(520,265);
        Font = new Font("Segoe UI",10); BackColor = Color.FromArgb(245,248,246);
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        var title = new Label { Text="AI CARE", Font=new Font("Segoe UI",21,FontStyle.Bold), ForeColor=Color.FromArgb(40,92,77), Location=new Point(26,22), AutoSize=true };
        Controls.Add(title);
        status = new Label { Text="Sẵn sàng khởi động", Location=new Point(28,81), Size=new Size(460,58) };
        Controls.Add(status);
        Button open = MakeButton("Mở cổng web",28,151); open.Click += delegate { if(url!=null) Process.Start(new ProcessStartInfo(url+"/login") { UseShellExecute=true }); };
        Button data = MakeButton("Dữ liệu",188,151); data.Click += delegate { Process.Start("explorer.exe",Quote(home)); };
        Button logs = MakeButton("Xem log",348,151); logs.Click += delegate { Process.Start("explorer.exe",Quote(Path.Combine(home,"logs"))); };
        var hint=new Label { Text="Đóng cửa sổ này để dừng hệ thống an toàn.",Location=new Point(28,218),AutoSize=true,ForeColor=Color.Gray };Controls.Add(hint);
        Shown += async delegate { try { await Task.Run(()=>StartBackend(false)); status.Text="Đã sẵn sàng. Ứng dụng người cao tuổi và cổng web đang chạy."; Process.Start(new ProcessStartInfo(url+"/login") { UseShellExecute=true }); } catch(Exception ex) { status.Text="Không khởi động được: "+ex.Message; MessageBox.Show(ex.Message+"\nXem log tại: "+home,"AI Care",MessageBoxButtons.OK,MessageBoxIcon.Error); } };
        FormClosing += async delegate(object sender,FormClosingEventArgs e) { if(closing)return; e.Cancel=true; closing=true; status.Text="Đang dừng ứng dụng và máy chủ..."; await Task.Run(()=>StopBackend()); Close(); };
    }
    Button MakeButton(string label,int x,int y) { var b=new Button { Text=label,Location=new Point(x,y),Size=new Size(144,44),FlatStyle=FlatStyle.Flat }; b.FlatAppearance.BorderColor=Color.FromArgb(210,222,215);Controls.Add(b);return b; }
    static string Quote(string value) { return "\""+value.Replace("\"","\\\"")+"\""; }
    void Append(string text) { if(text==null)return;lock(logLock){if(log!=null){log.WriteLine(text);log.Flush();}} }
    string Request(string path,string method,bool authenticated) {
        var request=(HttpWebRequest)WebRequest.Create(url+path); request.Method=method;request.Proxy=null;request.Timeout=5000;
        if(authenticated)request.Headers["X-AICare-Launcher"]=token;
        if(method=="POST")request.ContentLength=0;
        using(var response=(HttpWebResponse)request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream()))return reader.ReadToEnd();
    }
    void StartBackend(bool selfTest) {
        Directory.CreateDirectory(Path.Combine(home,"launcher"));
        readyFile=Path.Combine(home,"launcher","ready-"+Guid.NewGuid().ToString("N")+".json");
        token=Guid.NewGuid().ToString("N")+Guid.NewGuid().ToString("N");
        var info=new ProcessStartInfo(Path.Combine(root,"runtime","python.exe"));
        info.Arguments="-I "+Quote(Path.Combine(root,"app","portable_entry.py"))+" --home "+Quote(home)+" --port 0 --token "+token+" --ready-file "+Quote(readyFile)+(selfTest?" --no-desktop":"");
        info.WorkingDirectory=Path.Combine(root,"app");info.UseShellExecute=false;info.CreateNoWindow=true;info.RedirectStandardOutput=true;info.RedirectStandardError=true;
        log=new StreamWriter(Path.Combine(home,"logs","server-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".log"),true,System.Text.Encoding.UTF8);
        info.StandardOutputEncoding=System.Text.Encoding.UTF8;info.StandardErrorEncoding=System.Text.Encoding.UTF8;
        backend=new Process();backend.StartInfo=info;backend.OutputDataReceived+=(s,e)=>Append(e.Data);backend.ErrorDataReceived+=(s,e)=>Append(e.Data);
        if(!backend.Start())throw new Exception("Không mở được Python đi kèm.");
        backend.BeginOutputReadLine();backend.BeginErrorReadLine();
        DateTime deadline=DateTime.UtcNow.AddSeconds(180);
        while(DateTime.UtcNow<deadline) {
            if(backend.HasExited)throw new Exception("Python dừng với mã "+backend.ExitCode+". Xem log trong thư mục dữ liệu.");
            if(File.Exists(readyFile)) {
                try {
                    var state=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(readyFile));
                    url=(string)state["url"];
                    if(!url.StartsWith("http://127.0.0.1:",StringComparison.Ordinal))throw new Exception("Địa chỉ máy chủ không hợp lệ.");
                    if(Request("/_portable/health","GET",true).Contains("true"))return;
                } catch(IOException){} catch(WebException){}
            }
            Thread.Sleep(250);
        }
        throw new Exception("Quá thời gian chờ khởi động mô hình (180 giây).");
    }
    void StopBackend() {
        if(backend!=null && !backend.HasExited) {
            try { Request("/_portable/shutdown","POST",true); }catch(Exception ex){Append(ex.Message);}
            if(!backend.WaitForExit(40000)) {
                // Only the process tree created by this launcher is a fallback target.
                var info=new ProcessStartInfo("taskkill.exe","/PID "+backend.Id+" /T /F") {UseShellExecute=false,CreateNoWindow=true};
                using(var killer=Process.Start(info)) { killer.WaitForExit(5000); }
                backend.WaitForExit(5000);
            }
        }
        if(log!=null){lock(logLock){log.Dispose();log=null;}}
        if(readyFile!=null && File.Exists(readyFile))File.Delete(readyFile);
    }
    public int SelfTest() {
        try {
            StartBackend(true);
            bool denied=false;
            try { Request("/_portable/shutdown","POST",false); }catch(WebException ex){if(ex.Response!=null)denied=((HttpWebResponse)ex.Response).StatusCode==HttpStatusCode.Forbidden;}
            if(!denied)throw new Exception("Control endpoint accepted a missing token.");
            StopBackend();
            if(backend==null || !backend.HasExited || backend.ExitCode!=0)throw new Exception("Shutdown did not finish cleanly.");
            File.WriteAllText(Path.Combine(home,"logs","launcher-self-test.json"),"{\"passed\":true,\"health\":true,\"unauthorized_shutdown_denied\":true,\"exit_code\":0}");return 0;
        } catch(Exception ex) {try {StopBackend();}catch{}File.WriteAllText(Path.Combine(home,"logs","launcher-self-test.json"),new JavaScriptSerializer().Serialize(new {passed=false,error=ex.ToString()}));return 1;}
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string edition=File.ReadAllText(Path.Combine(root,"EDITION.txt")).Trim();
        bool selfTest=Array.IndexOf(args,"--self-test")>=0;
        bool created;
        using(var mutex=new Mutex(true,"Local\\AICare.Portable."+edition+(selfTest?".SelfTest":""),out created)) {
            if(!created){if(!selfTest)MessageBox.Show("AI Care đang mở. Vui lòng sử dụng cửa sổ hiện tại.");return 0;}
            using(var form=new AICareLauncher()) {
                if(selfTest)return form.SelfTest();
                Application.Run(form);return 0;
            }
        }
    }
}
