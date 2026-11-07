// Vesya personal Chrome launcher for Windows.
// The private download endpoint appends JSON configuration after CONFIG_MAGIC.
package main

import (
 "bufio"
 "bytes"
 "crypto/sha256"
 "crypto/tls"
 "encoding/base64"
 "encoding/hex"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "net"
 "net/http"
 "net/url"
 "os"
 "os/exec"
 "path/filepath"
 "strconv"
 "strings"
 "sync/atomic"
 "syscall"
 "time"
 "unsafe"
)

const CONFIG_MAGIC = "\nVESYA_PROXY_CONFIG_V1\n"
const localAddr = "127.0.0.1:43137"
const clientVersion = "v2.1"

type Config struct {
 Host string
 Port int
 Username string
 Password string
}

func load() (Config,error) {
 var c Config
 file,err:=os.Executable();if err!=nil{return c,err}
 f,err:=os.Open(file);if err!=nil{return c,err};defer f.Close()
 st,err:=f.Stat();if err!=nil{return c,err}
 if st.Size()<100||st.Size()>100000000{return c,errors.New("Неверный размер программы")}
 off:=st.Size()-4096;if off<0{off=0}
 if _,err=f.Seek(off,io.SeekStart);err!=nil{return c,err}
 tail,err:=io.ReadAll(io.LimitReader(f,4096));if err!=nil{return c,err}
 idx:=bytes.LastIndex(tail,[]byte(CONFIG_MAGIC))
 if idx<0{return c,errors.New("Нет настроек подключения")}
 payload:=tail[idx+len(CONFIG_MAGIC):]
 if len(payload)>1024||len(payload)<10{return c,errors.New("Неверные настройки подключения")}
 if err=json.Unmarshal(payload,&c);err!=nil{return c,err}
 if c.Host!="paruslegal.ru"||c.Port!=43128||!strings.HasPrefix(c.Username,"user")||len(c.Password)!=32{
  return c,errors.New("Некорректные параметры прокси")
 }
 return c,nil
}

func notice(message string) {
 title,_:=syscall.UTF16PtrFromString("Веся — ChatGPT")
 body,_:=syscall.UTF16PtrFromString(message)
 proc:=syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
 proc.Call(0,uintptr(unsafe.Pointer(body)),uintptr(unsafe.Pointer(title)),0x10)
}

func openChrome() error {
 candidates:=[]string{
  filepath.Join(os.Getenv("ProgramFiles"),"Google","Chrome","Application","chrome.exe"),
  filepath.Join(os.Getenv("ProgramFiles(x86)"),"Google","Chrome","Application","chrome.exe"),
  filepath.Join(os.Getenv("LOCALAPPDATA"),"Google","Chrome","Application","chrome.exe"),
 }
 path:=""
 for _,candidate:=range candidates{
  if st,err:=os.Stat(candidate);err==nil&&!st.IsDir(){path=candidate;break}
 }
 if path==""{return errors.New("Не найден Google Chrome")}
 profile:=filepath.Join(os.Getenv("LOCALAPPDATA"),"VesyaProxyChromeAutoV2")
 if err:=os.MkdirAll(profile,0700);err!=nil{return err}
 command:=exec.Command(path,
  "--user-data-dir="+profile,
  "--proxy-server=http://"+localAddr,
  "--disable-quic",
  "--disable-extensions",
  "--no-first-run",
  "--no-default-browser-check",
  "https://chatgpt.com/")
 if err:=command.Start();err!=nil{return err}
 return command.Process.Release()
}

type Proxy struct {
 Config Config
 Transport *http.Transport
 Active atomic.Int64
 Connected atomic.Uint64
 Failures atomic.Uint64
 LastError atomic.Value
}

func newProxy(c Config)*Proxy{
 upstream:=&url.URL{Scheme:"https",Host:net.JoinHostPort(c.Host,strconv.Itoa(c.Port)),User:url.UserPassword(c.Username,c.Password)}
 transport:=&http.Transport{Proxy:http.ProxyURL(upstream),TLSClientConfig:&tls.Config{MinVersion:tls.VersionTLS12},IdleConnTimeout:30*time.Second,ForceAttemptHTTP2:false}
 p:=&Proxy{Config:c,Transport:transport}
 p.LastError.Store("none")
 return p
}

func(p *Proxy) identity()string{
 h:=sha256.Sum256([]byte(p.Config.Host+":"+p.Config.Username))
 return hex.EncodeToString(h[:])
}

func(p *Proxy) ServeHTTP(w http.ResponseWriter,r *http.Request){
 if r.Method=="GET"&&r.URL.Path=="/__vesya_health"{
  w.Header().Set("Cache-Control","no-store")
  _,_=io.WriteString(w,"vesya:"+p.identity());return
 }
 if r.Method=="GET"&&r.URL.Path=="/__vesya_diag"{
  w.Header().Set("Content-Type","application/json")
  w.Header().Set("Cache-Control","no-store")
  _=json.NewEncoder(w).Encode(map[string]interface{}{
   "version":clientVersion,
   "active_tunnels":p.Active.Load(),
   "established_tunnels":p.Connected.Load(),
   "failed_connections":p.Failures.Load(),
   "last_error":p.LastError.Load(),
  })
  return
 }
 if r.Method==http.MethodConnect{p.connect(w,r);return}
 if r.URL.Scheme!="http"&&r.URL.Scheme!="https"{http.Error(w,"Bad URL",400);return}
 request:=r.Clone(r.Context())
 request.RequestURI=""
 request.Header.Del("Proxy-Authorization")
 request.Header.Del("Proxy-Connection")
 response,err:=p.Transport.RoundTrip(request)
 if err!=nil{p.failure("http_upstream");http.Error(w,"Remote proxy unavailable",502);return}
 defer response.Body.Close()
 for k,vs:=range response.Header{for _,v:=range vs{w.Header().Add(k,v)}}
 w.WriteHeader(response.StatusCode)
 _,_=io.Copy(w,response.Body)
}

func (p *Proxy) failure(kind string){
 p.Failures.Add(1)
 p.LastError.Store(kind)
}

func(p *Proxy) connect(w http.ResponseWriter,r *http.Request){
 host:=r.Host
 if host==""||!strings.Contains(host,":"){http.Error(w,"Missing host",400);return}
 c:=p.Config
 dialer:=&net.Dialer{Timeout:15*time.Second,KeepAlive:30*time.Second}
 remote,err:=tls.DialWithDialer(dialer,"tcp",net.JoinHostPort(c.Host,strconv.Itoa(c.Port)),&tls.Config{ServerName:c.Host,MinVersion:tls.VersionTLS12})
 if err!=nil{p.failure("tls_connect");http.Error(w,"Remote proxy unavailable",502);return}
 defer remote.Close()
 auth:=base64.StdEncoding.EncodeToString([]byte(c.Username+":"+c.Password))
 request:=fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\nProxy-Authorization: Basic %s\r\n\r\n",host,host,auth)
 _=remote.SetDeadline(time.Now().Add(20*time.Second))
 if _,err=io.WriteString(remote,request);err!=nil{p.failure("proxy_write");http.Error(w,"Remote proxy write failed",502);return}
 reader:=bufio.NewReader(remote)
 response,err:=http.ReadResponse(reader,&http.Request{Method:http.MethodConnect})
 if err!=nil{p.failure("proxy_read");http.Error(w,"Remote proxy rejected tunnel",502);return}
 if response.StatusCode!=200{
  response.Body.Close()
  p.failure("proxy_status_"+strconv.Itoa(response.StatusCode))
  http.Error(w,"Remote proxy rejected connection",502);return
 }
 _=remote.SetDeadline(time.Time{})
 hijacker,ok:=w.(http.Hijacker)
 if !ok{http.Error(w,"Tunnel not supported",500);return}
 client,buff,err:=hijacker.Hijack()
 if err!=nil{p.failure("local_hijack");return}
 defer client.Close()
 p.Connected.Add(1)
 p.Active.Add(1)
 defer p.Active.Add(-1)
 if _,err=buff.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n");err!=nil{return}
 if err=buff.Flush();err!=nil{return}
 done:=make(chan struct{})
 go func(){
  _,_=io.Copy(remote,buff)
  _=remote.CloseWrite()
  close(done)
 }()
 _,_=io.Copy(client,reader)
 if tcp,ok:=client.(*net.TCPConn);ok{_=tcp.CloseWrite()}
 select{
 case <-done:
 case <-time.After(10*time.Second):
  _=remote.Close()
  <-done
 }
}

func main(){
 config,err:=load()
 if err!=nil{notice("Не удалось подключиться: "+err.Error());return}
 p:=newProxy(config)
 listener,err:=net.Listen("tcp",localAddr)
 if err!=nil{
  hc:=&http.Client{Timeout:time.Second,Transport:&http.Transport{Proxy:nil}}
  response,e:=hc.Get("http://"+localAddr+"/__vesya_health")
  if e==nil{
   body,_:=io.ReadAll(io.LimitReader(response.Body,256));response.Body.Close()
   if string(body)=="vesya:"+p.identity(){
    if e=openChrome();e!=nil{notice(e.Error())}
    return
   }
  }
  notice("Локальный порт занят. Закройте прежний клиент Веси или перезагрузите компьютер.")
  return
 }
 defer listener.Close()
 server:=&http.Server{Handler:p,ReadHeaderTimeout:15*time.Second,IdleTimeout:60*time.Second}
 go func(){_=server.Serve(listener)}()
 if err=openChrome();err!=nil{notice(err.Error());_=server.Close();return}
 select{}
}
