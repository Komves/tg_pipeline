package main

import (
 "bufio"
 "crypto/tls"
 "encoding/base64"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "net"
 "net/http"
 "os"
 "os/exec"
 "path/filepath"
 "strconv"
 "strings"
 "syscall"
 "time"
 "unsafe"
)

// The public GPT.exe has no account credentials. The customer enters the
// login/password supplied privately by Vesya once. Windows DPAPI protects
// account.dat for this Windows user on this machine.
type account struct {
 Username string `json:"username"`
 Password string `json:"password"`
}
type dataBlob struct {
 Size uint32
 Data *byte
}
func blob(b []byte) dataBlob {
 if len(b)==0{return dataBlob{}}
 return dataBlob{Size:uint32(len(b)),Data:&b[0]}
}
func crypt(input []byte, decrypt bool)([]byte,error){
 library:=syscall.NewLazyDLL("crypt32.dll")
 function:="CryptProtectData"
 if decrypt{function="CryptUnprotectData"}
 proc:=library.NewProc(function)
 free:=syscall.NewLazyDLL("kernel32.dll").NewProc("LocalFree")
 in:=blob(input)
 entropy:=[]byte("GPT Windows launcher isolated profile v1")
 extra:=blob(entropy)
 out:=dataBlob{}
 success,_,_:=proc.Call(uintptr(unsafe.Pointer(&in)),0,
    uintptr(unsafe.Pointer(&extra)),0,0,0x1,uintptr(unsafe.Pointer(&out)))
 if success==0{return nil,errors.New("Windows не смогла прочитать защищённые настройки")}
 defer free.Call(uintptr(unsafe.Pointer(out.Data)))
 if out.Data==nil||out.Size==0||out.Size>16384{return nil,errors.New("Неверные защищённые настройки")}
 return append([]byte(nil),unsafe.Slice(out.Data,out.Size)...),nil
}
func localDir()(string,error){
 root:=os.Getenv("LOCALAPPDATA")
 if root==""{return "",errors.New("Нет папки LocalAppData")}
 base:=filepath.Join(root,"GPT")
 if err:=os.MkdirAll(base,0700);err!=nil{return "",err}
 return base,nil
}
func accountFile(base string)string{return filepath.Join(base,"account.dat")}
func validAccount(a account)bool{
 if len(a.Username)!=20||!strings.HasPrefix(a.Username,"gpt_"){return false}
 for _,r:=range a.Username[4:]{
  if !strings.ContainsRune("0123456789abcdef",r){return false}
 }
 return len(a.Password)>=16&&len(a.Password)<=256&&!strings.ContainsAny(a.Password,"
")
}
func readAccount(base string)(account,error){
 var a account
 data,err:=os.ReadFile(accountFile(base));if err!=nil{return a,err}
 plaintext,err:=crypt(data,true);if err!=nil{return a,err}
 if err=json.Unmarshal(plaintext,&a);err!=nil{return a,err}
 if !validAccount(a){return a,errors.New("Неверный формат учётной записи")}
 return a,nil
}
func storeAccount(base string,a account)error{
 if !validAccount(a){return errors.New("Неверные параметры учётной записи")}
 plaintext,err:=json.Marshal(a);if err!=nil{return err}
 ciphertext,err:=crypt(plaintext,false);if err!=nil{return err}
 temp:=accountFile(base)+".tmp"
 if err=os.WriteFile(temp,ciphertext,0600);err!=nil{return err}
 if err=os.Rename(temp,accountFile(base));err!=nil{_ = os.Remove(temp);return err}
 return nil
}
type credUIInfo struct {
 Size uint32
 Parent uintptr
 Message *uint16
 Caption *uint16
 Banner uintptr
}
func promptAccount()(account,error){
 var a account
 msg,_:=syscall.UTF16PtrFromString("Введи логин и пароль, которые прислала Веся. Пароль будет защищён средствами Windows.")
 caption,_:=syscall.UTF16PtrFromString("GPT — подключение")
 target,_:=syscall.UTF16PtrFromString("GPT HTTPS Proxy")
 ui:=credUIInfo{Size:uint32(unsafe.Sizeof(credUIInfo{})),Message:msg,Caption:caption}
 var username [513]uint16
 var password [257]uint16
 var save uint32
 const flags=0x40000|0x80|0x2|0x8 // generic, always show, no persistence, no certificates
 status,_,_:=syscall.NewLazyDLL("credui.dll").NewProc("CredUIPromptForCredentialsW").Call(
  uintptr(unsafe.Pointer(&ui)),uintptr(unsafe.Pointer(target)),0,0,
  uintptr(unsafe.Pointer(&username[0])),uintptr(len(username)),
  uintptr(unsafe.Pointer(&password[0])),uintptr(len(password)),
  uintptr(unsafe.Pointer(&save)),uintptr(flags))
 if status==1223{return a,errors.New("Ввод отменён")}
 if status!=0{return a,fmt.Errorf("Ошибка окна ввода Windows (%d)",status)}
 a.Username=strings.TrimSpace(syscall.UTF16ToString(username[:]))
 a.Password=syscall.UTF16ToString(password[:])
 for i:=range password{password[i]=0}
 if !validAccount(a){return a,errors.New("Проверь логин gpt_... и пароль из сообщения Веси")}
 return a,nil
}
func verifyAccount(a account)error{
 dialer:=&net.Dialer{Timeout:8*time.Second}
 connection,err:=tls.DialWithDialer(dialer,"tcp",net.JoinHostPort("ru.paruslegal.ru","4454"),
  &tls.Config{ServerName:"ru.paruslegal.ru",MinVersion:tls.VersionTLS12})
 if err!=nil{return errors.New("Сервер GPT не отвечает")}
 defer connection.Close()
 _=connection.SetDeadline(time.Now().Add(12*time.Second))
 authorization:=base64.StdEncoding.EncodeToString([]byte(a.Username+":"+a.Password))
 request:=fmt.Sprintf("CONNECT chatgpt.com:443 HTTP/1.1
Host: chatgpt.com:443
Proxy-Authorization: Basic %s

",authorization)
 if _,err=io.WriteString(connection,request);err!=nil{return errors.New("Нет связи с прокси GPT")}
 response,err:=http.ReadResponse(bufio.NewReader(connection),&http.Request{Method:http.MethodConnect})
 if err!=nil{return errors.New("Нет ответа от прокси GPT")}
 defer response.Body.Close()
 switch response.StatusCode{
 case 200:return nil
 case 407:return errors.New("Логин или пароль не принят либо доступ отключён. Сверь данные с сообщением Веси")
 default:return fmt.Errorf("Прокси GPT не разрешил подключение (HTTP %d)",response.StatusCode)
 }
}
func loadAccount()(Config,error){
 c:=Config{Host:"ru.paruslegal.ru",Port:4454}
 base,err:=localDir();if err!=nil{return c,err}
 a,err:=readAccount(base)
 if err!=nil{
  a,err=promptAccount();if err!=nil{return c,err}
  if err=verifyAccount(a);err!=nil{return c,err}
  if err=storeAccount(base,a);err!=nil{return c,err}
 }
 c.Username=a.Username
 c.Password=a.Password
 return c,nil
}
func psQuote(raw string)string{return strings.ReplaceAll(raw,"'","''")}
func shortcut(program string)error{
 script:="$ErrorActionPreference='Stop';"+
  "$desk=[Environment]::GetFolderPath('Desktop');"+
  "$shell=New-Object -ComObject WScript.Shell;"+
  "$s=$shell.CreateShortcut((Join-Path $desk 'GPT.lnk'));"+
  "$s.TargetPath='"+psQuote(program)+"';"+
  "$s.WorkingDirectory='"+psQuote(filepath.Dir(program))+"';"+
  "$s.Description='GPT - отдельный Chrome';"+
  "$s.Save()"
 cmd:=exec.Command("powershell.exe","-NoProfile","-NonInteractive","-WindowStyle","Hidden","-Command",script)
 cmd.SysProcAttr=&syscall.SysProcAttr{HideWindow:true}
 return cmd.Run()
}
func installToLocalAppData()(bool,error){
 base,err:=localDir();if err!=nil{return false,err}
 if len(os.Args)>1&&os.Args[1]=="--reset"{_ = os.Remove(accountFile(base))}
 source,err:=os.Executable();if err!=nil{return false,err}
 installed:=filepath.Join(base,"GPT.exe")
 if !strings.EqualFold(filepath.Clean(source),filepath.Clean(installed)){
  input,err:=os.Open(source);if err!=nil{return false,err};defer input.Close()
  output,err:=os.OpenFile(installed,os.O_CREATE|os.O_TRUNC|os.O_WRONLY,0700)
  if err!=nil{return false,err}
  if _,err=io.Copy(output,input);err!=nil{output.Close();return false,err}
  if err=output.Close();err!=nil{return false,err}
  if err=shortcut(installed);err!=nil{return false,errors.New("Не удалось создать ярлык GPT")}
  child:=exec.Command(installed)
  if err=child.Start();err!=nil{return false,err}
  _=child.Process.Release()
  return true,nil
 }
 if err=shortcut(installed);err!=nil{return false,errors.New("Не удалось создать ярлык GPT")}
 return false,nil
}

// This is not a VPN, nor an app-wide Windows proxy; only the isolated Chrome
// launched by GPT.exe uses the loopback helper. Old Vesya /proxy is untouched.
