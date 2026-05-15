console.debug("%cgosuslugi.plugin.extension.debugger load", "font-weight: bold;");

/*
     menu
*/
const _id = "debugger";
const _ping = "ping";

var _callbacks = {};

const _menu = [{
     anchor: "main",
     links: [{
          link: "common_link",
          tab: "common"
     },
     {
          link: "certificates_link",
          tab: "certificates"
     },
     {
          link: "access_lists_link",
          tab: "access_lists"
     },
     {
          link: "providers_link",
          tab: "providers"
     },
     {
          link: "tokens_link",
          tab: "tokens"
     },
     {
          link: "modules_link",
          tab: "modules"
     },
     {
          link: "debug_link",
          tab: "debug"
     },
     {
          link: "about_link",
          tab: "about"
     }]
},
     {
          anchor: "certificates",
          links: [{
               link: "address_book_certificates_link",
               tab: "address_book_certificates"
          },
          {
               link: "auth_root_certificates_link",
               tab: "auth_root_certificates"
          },
          {
               link: "certificate_authority_certificates_link",
               tab: "certificate_authority_certificates"
          },
          {
               link: "disallowed_certificates_link",
               tab: "disallowed_certificates"
          },
          {
               link: "my_certificates_link",
               tab: "my_certificates"
          },
          {
               link: "root_certificates_link",
               tab: "root_certificates"
          },
          {
               link: "trusted_people_certificates_link",
               tab: "trusted_people_certificates"
          },
          {
               link: "trusted_publisher_certificates_link",
               tab: "trusted_publisher_certificates"
          },
          {
               link: "rutoken_certificates_link",
               tab: "rutoken_certificates"
          },
          {
               link: "jacarta_certificates_link",
               tab: "jacarta_certificates"
          },
          {
               link: "esmart_certificates_link",
               tab: "esmart_certificates"
          },
          {
               link: "review_lists_link",
               tab: "review_lists"
          }]
     },
     {
          anchor: "access_lists",
          links: [{
               link: "granted_nodes_link",
               tab: "granted_nodes"
          },
          {
               link: "denied_nodes_link",
               tab: "denied_nodes"
          }]
     },
     {
          anchor: "debug",
          links: [{
               link: "debug_common_link",
               tab: "debug_common"
          },
          {
               link: "debug_content_link",
               tab: "debug_content"
          },
          {
               link: "debug_raw_link",
               tab: "debug_raw"
          },
          {
               link: "debug_base64_link",
               tab: "debug_base64"
          },
          {
               link: "debug_system_link",
               tab: "debug_system"
          },
          {
               link: "debug_crypto_link",
               tab: "debug_crypto"
          }
          ]
     }
];


const systemCertificate = new GosuslugiPluginFileInfo("MIIEB...", "", "base64");
const tokenCertificate = new GosuslugiPluginFileInfo("MIIG0T...", "", "base64");

const testAttachedSignature = new GosuslugiPluginFileInfo("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", "", "base64");
const testDetachedSignature = new GosuslugiPluginFileInfo("MIAGCSqGSIb3DQEHAqCAMIACAQExDjAMBggqhQMHAQECAgUAMIAGCSqGSIb3DQEHAQAAoIIFXDCCBVgwggUDoAMCAQICEAHV1ngYDlYwAAAAACmOAAEwDAYIKoUDBwEBAwIFADCBhDELMAkGA1UEBhMCUlUxFTATBgNVBAcMDNCc0L7RgdC60LLQsDEUMBIGA1UECgwL0KPQpiDQn9Ck0KAxDTALBgNVBAsMBNCj0KYxIzAhBgNVBAwMGtCQ0LTQvNC40L3QuNGB0YLRgNCw0YLQvtGAMRQwEgYDVQQDDAvQo9CmINCf0KTQoDAeFw0yMDAxMjkwNzQ2MDBaFw0yMTA0MjkwNzQ2MDBaMIIBXDEfMB0GA1UEAwwW0J/QvtC90LXQtNC10LvRjNC90LjQujEfMB0GA1UECgwW0J/QvtC90LXQtNC10LvRjNC90LjQujEKMAgGA1UECwwBMTERMA8GA1UEDAwI0KLQtdGB0YIxCzAJBgNVBAYTAlJVMSswKQYDVQQIDCIwNCDQoNC10YHQv9GD0LHQu9C40LrQsCDQkNC70YLQsNC5MRUwEwYDVQQHDAzQnNC+0YHQutCy0LAxIzAhBgNVBAQMGtCf0L7QvdC10LTQtdC70YzQvdC40LrQvtCyMRUwEwYDVQQqDAzQodC10YDQs9C10LkxHjAcBgNVBAkMFdCQ0LvRgtCw0LnRgdC60LDRjywgMjEaMBgGCCqFAwOBAwEBEgwwMDY4MDUyMjQxNzAxGDAWBgUqhQNkARINNDY1OTI5NDE0NTIzMTEWMBQGBSqFA2QDEgswMDEwMDI2NTg1MTBmMB8GCCqFAwcBAQEBMBMGByqFAwICIwEGCCqFAwcBAQICA0MABEC8tg3qsdIwKR/SDNh/q3DKuUUfqtOtNVfw5ZGGbRQphwMD9oHR9BqfPqGq/1+613o80g12GGitIMsGGbrloOKggQkAMjk4RTAwMDGjggJgMIICXDALBgNVHQ8EBAMCBPAwHQYDVR0lBBYwFAYIKwYBBQUHAwIGCCsGAQUFBwMEMAwGA1UdEwEB/wQCMAAwGQYFKoUDZG8EEAwOVmlQTmV0IENTUCA0LjIwggECBgUqhQNkcASB+DCB9QxC0KHQmtCX0JggIlZpUE5ldCBDU1AgNC4yIiAgKNCy0LDRgNC40LDQvdGCINC40YHQv9C+0LvQvdC10L3QuNGPIDIpDFjQn9GA0L7Qs9GA0LDQvNC80L3Ri9C5INC60L7QvNC/0LvQtdC60YEgVmlQTmV0INCj0LTQvtGB0YLQvtCy0LXRgNGP0Y7RidC40Lkg0YbQtdC90YLRgCA0DCbQodCkLzEyNC0zNDMzINC+0YIgNiDQuNGO0LvRjyAyMDE4INCzLgwt0KHQpC8xMTgtMzUxMCDQvtGCIDI1INC+0LrRgtGP0LHRgNGPIDIwMTgg0LMuMB0GA1UdIAQWMBQwCAYGKoUDZHEBMAgGBiqFA2RxAjCBwAYDVR0jBIG4MIG1gBT7ygT+0s4mlK0g+nPtrzXm/CyU06GBiqSBhzCBhDELMAkGA1UEBhMCUlUxFTATBgNVBAcMDNCc0L7RgdC60LLQsDEUMBIGA1UECgwL0KPQpiDQn9Ck0KAxDTALBgNVBAsMBNCj0KYxIzAhBgNVBAwMGtCQ0LTQvNC40L3QuNGB0YLRgNCw0YLQvtGAMRQwEgYDVQQDDAvQo9CmINCf0KTQoIIQAdUMk+k8zyAAAAAAKY4AATAdBgNVHQ4EFgQUSP7JB62a94WhskNqjFz/IICu19gwDAYIKoUDBwEBAwIFAANBAE7EEti9WRa+JwymoYzAGgMfM6RENuZ+cutGRvPnQB+vGoekHj0ORKQp2lfSZ6hID6FauQ4xSR29X5NlbnKUKbsxggJcMIICWAIBATCBmTCBhDELMAkGA1UEBhMCUlUxFTATBgNVBAcMDNCc0L7RgdC60LLQsDEUMBIGA1UECgwL0KPQpiDQn9Ck0KAxDTALBgNVBAsMBNCj0KYxIzAhBgNVBAwMGtCQ0LTQvNC40L3QuNGB0YLRgNCw0YLQvtGAMRQwEgYDVQQDDAvQo9CmINCf0KTQoAIQAdXWeBgOVjAAAAAAKY4AATAMBggqhQMHAQECAgUAoIIBVzAYBgkqhkiG9w0BCQMxCwYJKoZIhvcNAQcBMBwGCSqGSIb3DQEJBTEPFw0yMDA5MTAxNTEyMTZaMC8GCSqGSIb3DQEJBDEiBCAZc9a5Gw4GCbctiDC9QNGw8LrbLF4e7j4vxQJ6GqAwKjCB6wYLKoZIhvcNAQkQAi8xgdswgdgwgdUwgdIwDAYIKoUDBwEBAgIFAAQgFXzKR1ie9sR65Qe4RoH6Ry9DIFXmuw5m+9u7QRQx/zUwgZ8wgYqkgYcwgYQxCzAJBgNVBAYTAlJVMRUwEwYDVQQHDAzQnNC+0YHQutCy0LAxFDASBgNVBAoMC9Cj0KYg0J/QpNCgMQ0wCwYDVQQLDATQo9CmMSMwIQYDVQQMDBrQkNC00LzQuNC90LjRgdGC0YDQsNGC0L7RgDEUMBIGA1UEAwwL0KPQpiDQn9Ck0KACEAHV1ngYDlYwAAAAACmOAAEwDAYIKoUDBwEBAQEFAARA0GX/T4uQDkvvxhfy4hsBcGsz0iGvoxmmO9V+NmGJ5UVNewaVG2i+wpxaZj79XfH201f8RfkkQDg1BlcnpkeHaAAAAAAAAA==", "", "base64");

const testXml = new GosuslugiPluginFileInfo(Base64.encode(String.raw `<?xml version="1.0" encoding="UTF-8"?>
     <elementOne>
          Text1
          <element2>
               Text2
               <element3>
                    Text3
                    <element4>
                         Text4
                    </element4>
               </element3>
          </element2>
     </elementOne>`), "", "base64");

const testXmlSignature = new GosuslugiPluginFileInfo(Base64.encode(String.raw `<?xml version="1.0" encoding="utf-8" standalone="no" ?><elementOne>
          Text1
          <element2>
               Text2
               <element3>
                    Text3
                    <element4>
                         Text4
                    </element4>
               </element3>
          </element2>
     <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
<ds:SignedInfo>
<ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
<ds:SignatureMethod Algorithm="urn:ietf:params:xml:ns:cpxmlsec:algorithms:gostr34102012-gostr34112012-256"/>
<ds:Reference URI="">
<ds:Transforms>
<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
<ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>
</ds:Transforms>
<ds:DigestMethod Algorithm="urn:ietf:params:xml:ns:cpxmlsec:algorithms:gostr34112012-256"/>
<ds:DigestValue>Z//IzqQnrc3m0VK+bQ7VrEHJMwLUktKb1FC3NAdwSJ0=</ds:DigestValue>
</ds:Reference>
</ds:SignedInfo>
<ds:SignatureValue>qFdpEP+opvT+4tv3lRihVGMP3RmV96r7+v6LnGpg1nuRIoN5eRHfTxjIXwQUBO2K
kdPuWR6rVHZ2cte2Ka2PHw==
</ds:SignatureValue>
<ds:KeyInfo>
<ds:X509Data>
<ds:X509Certificate>MIIFWDCCBQOgAwIBAgIQAdXV3CBNDqAAAAAAKY4AATAMBggqhQMHAQEDAgUAMIGEMQswCQYDVQQGEwJSVTEVMBMGA1UEBwwM0JzQvtGB0LrQstCwMRQwEgYDVQQKDAvQo9CmINCf0KTQoDENMAsGA1UECwwE0KPQpjEjMCEGA1UEDAwa0JDQtNC80LjQvdC40YHRgtGA0LDRgtC+0YAxFDASBgNVBAMMC9Cj0KYg0J/QpNCgMB4XDTIwMDEyODEzMDkwMFoXDTIxMDQyODEzMDkwMFowggFcMR8wHQYDVQQDDBbQn9C+0L3QtdC00LXQu9GM0L3QuNC6MR8wHQYDVQQKDBbQn9C+0L3QtdC00LXQu9GM0L3QuNC6MQowCAYDVQQLDAExMREwDwYDVQQMDAjQotC10YHRgjELMAkGA1UEBhMCUlUxKzApBgNVBAgMIjA0INCg0LXRgdC/0YPQsdC70LjQutCwINCQ0LvRgtCw0LkxFTATBgNVBAcMDNCc0L7RgdC60LLQsDEjMCEGA1UEBAwa0J/QvtC90LXQtNC10LvRjNC90LjQutC+0LIxFTATBgNVBCoMDNCh0LXRgNCz0LXQuTEeMBwGA1UECQwV0JDQu9GC0LDQudGB0LrQsNGPLCAyMRowGAYIKoUDA4EDAQESDDAwNjgwNTIyNDE3MDEYMBYGBSqFA2QBEg00NjU5Mjk0MTQ1MjMxMRYwFAYFKoUDZAMSCzE1NTcxOTY5NjAyMGYwHwYIKoUDBwEBAQEwEwYHKoUDAgIjAQYIKoUDBwEBAgIDQwAEQJzD9DycG3iKwDPLEKrUpkpo/SOIGjuzV5FnKR3+00cj5PIv5q+4+Vs50KjiPKTlmwrsbzio2kFmj4CFVk4pJfCBCQAyOThFMDAwMaOCAmAwggJcMAsGA1UdDwQEAwIE8DAdBgNVHSUEFjAUBggrBgEFBQcDAgYIKwYBBQUHAwQwDAYDVR0TAQH/BAIwADAZBgUqhQNkbwQQDA5WaVBOZXQgQ1NQIDQuMjCCAQIGBSqFA2RwBIH4MIH1DELQodCa0JfQmCAiVmlQTmV0IENTUCA0LjIiICAo0LLQsNGA0LjQsNC90YIg0LjRgdC/0L7Qu9C90LXQvdC40Y8gMikMWNCf0YDQvtCz0YDQsNC80LzQvdGL0Lkg0LrQvtC80L/Qu9C10LrRgSBWaVBOZXQg0KPQtNC+0YHRgtC+0LLQtdGA0Y/RjtGJ0LjQuSDRhtC10L3RgtGAIDQMJtCh0KQvMTI0LTM0MzMg0L7RgiA2INC40Y7Qu9GPIDIwMTgg0LMuDC3QodCkLzExOC0zNTEwINC+0YIgMjUg0L7QutGC0Y/QsdGA0Y8gMjAxOCDQsy4wHQYDVR0gBBYwFDAIBgYqhQNkcQEwCAYGKoUDZHECMIHABgNVHSMEgbgwgbWAFPvKBP7SziaUrSD6c+2vNeb8LJTToYGKpIGHMIGEMQswCQYDVQQGEwJSVTEVMBMGA1UEBwwM0JzQvtGB0LrQstCwMRQwEgYDVQQKDAvQo9CmINCf0KTQoDENMAsGA1UECwwE0KPQpjEjMCEGA1UEDAwa0JDQtNC80LjQvdC40YHRgtGA0LDRgtC+0YAxFDASBgNVBAMMC9Cj0KYg0J/QpNCgghAB1QyT6TzPIAAAAAApjgABMB0GA1UdDgQWBBSuV8p9GxZ3KhIohNeecrysxb4imzAMBggqhQMHAQEDAgUAA0EARu+64qhoB8IoA3IPABpUg/R1UGKNf+dfdpnj9L0tOlAXQwxYvPQWK83AW6WsVwnxfsTyEMA4juViO07OL6JdLA==</ds:X509Certificate>
</ds:X509Data>
</ds:KeyInfo>
</ds:Signature></elementOne>`), "", "base64");


const testXmlTransform = new GosuslugiPluginFileInfo(String.raw `<?xml version="1.0" encoding="UTF-8"?>
<!--
 &amp; not replaces
 &lt; not replaces
 &gt; not replaces
 &quot; replaces with "
 &apos; replaces with '
-->
 <ns1:elementTwo xmlns:ns1="http://test/2">test&amp;&lt;&gt;&quot;&apos;"'</ns1:elementTwo>
`, "", "");

const testXmlDetachedSignature = new GosuslugiPluginFileInfo(String.raw `<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="urn://x-artefacts-smev-gov-ru/services/message-exchange/types/1.1" xmlns:ns1="urn://x-artefacts-smev-gov-ru/services/message-exchange/types/basic/1.1">
   <soapenv:Header/>
   <soapenv:Body>
      <ns:AckRequest>
         <ns2:AckTargetMessage xmlns:ns2="urn://x-artefacts-smev-gov-ru/services/message-exchange/types/basic/1.1" xmlns="urn://x-artefacts-smev-gov-ru/services/message-exchange/types/1.1" Id="SIGNED_BY_CALLER" accepted="true">0e8cfc01-5e81-11e4-a9ff-d4c9eff07b77</ns2:AckTargetMessage>
         <ns:CallerInformationSystemSignature></ns:CallerInformationSystemSignature>
      </ns:AckRequest>
   </soapenv:Body>
</soapenv:Envelope>`, "", "");

const testSignXmlElement = "/*[local-name()='elementOne']/*[local-name()='elementTwo']/*[local-name()='elementThree']";
const test1KbContent = new GosuslugiPluginFileInfo("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "", "");
const testContent = new GosuslugiPluginFileInfo( Base64.encode("unsigned file content"), "", "base64", "id1", "test.txt");

const uri_smev_transform = "urn://smev-gov-ru/xmldsig/transform";
const uri_exc_c14n_noc   = "http://www.w3.org/2001/10/xml-exc-c14n#";
const uri_envelope       = "http://www.w3.org/2000/09/xmldsig#enveloped-signature";


const tokenStore = "Rutoken ECP 3cbc5bb3";
const providerStore = "Infotecs GOST 2012/512 Cryptographic Service Provider";

const rawDefault = String.raw 
`
{
    "id": "d918dd1e-3d82-4f1c-b258-a5b74dea9f952",
    "node": "",
    "error": "",
    "mode": "debug",
    "module": {
        "type": "crypto",
        "version": "0.0.0.0"
    },
    "method": {
        "type": "signature",
        "data": "{\"files\":[{\"content\":\"c2lnbiBtZQ==\",\"path\":\"\",\"id\":\"1\",\"name\":\"sign.txt\",\"contentEncoding\":\"base64\",\"contentFormat\":\"\"}],\"certificate\":{\"content\":\"\",\"id\":\"\",\"name\":\"\",\"contentEncoding\":\"\",\"contentFormat\":\"\"},\"visualization\":{\"persistent\":\"false\"},\"type\":\"attached\"}",
        "result": ""
    },
    "meta": {
        "format": {
            "type": "message",
            "version": "0.0.0.0",
            "category": "gosuslugi.plugin"
        },
        "content": {
            "type": "application/json",
            "charset": "utf8",
            "transferEncoding": ""
        },
        "routes": [{
                "node": "gosuslugi.plugin.extension.crypto",
                "time": "02.09.2024, 16:25:46",
                "transfer": ""
            }
        ],
        "userAgent": "",
        "id": "",
        "chunk": "",
        "chunks": ""
    }
}
`;

/*
     utilities
*/
function сhangeTab(anchor, tab) {
     $("#" + anchor + " > nav a").removeClass('active');
     $("#" + anchor + " > div").removeClass('active');
     $("#" + anchor + " ." + tab).addClass('active');
};

function bind(id, action, func) {
     let element = document.getElementById(id);
     if (element) {
          element.addEventListener(action, func);
     }
     return !!element;
};

function dateFromTimestamp(timestamp) {
     let date = new Date(timestamp * 1000);

     return ("0" + (date.getDate()     )).slice(-2) + "." +
            ("0" + (date.getMonth() + 1)).slice(-2) + "." +
            (      (date.getFullYear() ));
};

function checkModulesInstance() {
     let instance;

     instance = typeof _system_0db7d9ec48754f9080a77e237238453b !== "undefined";
     {
          document.getElementById("system_js").textContent = typeof system !== "undefined" && system?.hasOwnProperty('caller') ? "system.js" : "system.js*";
          document.getElementById("system_js").style.color = instance ? "black" : "red";
          document.getElementById("debug_system_link").style.backgroundColor = instance ? "white" : "red";
     };

     instance = typeof _crypto_0db7d9ec48754f9080a77e237238453b !== "undefined";
     {
          document.getElementById("crypto_js").textContent = typeof crypto !== "undefined" && crypto?.hasOwnProperty('caller') ? "crypto.js" : "crypto.js*";
          document.getElementById("crypto_js").style.color = instance ? "black" : "red";
          document.getElementById("debug_crypto_link").style.backgroundColor = instance ? "white" : "red";
     };
     
     instance = typeof _formats_0db7d9ec48754f9080a77e237238453b !== "undefined";
     {
          document.getElementById("formats_js").style.color = instance ? "black" : "red";
     };
     
     instance = typeof _bootstrap_0db7d9ec48754f9080a77e237238453b !== "undefined";
     {
          document.getElementById("bootstrap_js").style.color = instance ? "black" : "red";
     };

     instance = typeof _crypto_wrapper_0db7d9ec48754f9080a77e237238453b !== "undefined";
     {
          document.getElementById("crypto_wrapper_js").style.color = instance ? "black" : "red";
     };
}

function checkExtensionInstance() {
     let updateState = function(result) {
          document.getElementById("debug_extension_text").innerHTML   = result ? "Расширение установлено" : "Расширение не установлено";
          document.getElementById("debug_extension_text").style.color = result ? 'black' : 'red';
          document.getElementById("debug_extension_checkbox").checked = result;          
     };

     updateState(false);

     const message = new GosuslugiPluginMessage(_id, "bootstrap", "registered");
     _callbacks[ message.id ] = function (message) {
          let result = false;

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               const registered = JSON.parse(message.method.result);
               result = registered.some(str => str.includes("content"));
          }

          updateState(result);
     };

     window.postMessage(message, '*');
}

function getModulesConfiguration() {
     let element = document.getElementById("debug_modules");
     element.innerHTML = "отсутствуют";

     let message = new GosuslugiPluginMessage(_id, "system", "config", "");
     _callbacks[ message.id ] = function (message) {

          let text = "<br/>";

          if (message.error && message.error.length) {
               notifier.notify(message.error);
          } else {
               let result = JSON.parse(message.method.result);

               result.forEach(function (each) {
                    text += [each.format.type, ": ", each.format.version, "<br/>"].join("");
               });
          }

          element.innerHTML = text;
     };

     window.postMessage(message, '*');
}


function systemCallBack(message) {
     /*
          результат может быть как сериализованным объектом, так и просто строкой,
          если объект, то выводим в отформатированном виде
     */
     try
     {
          let obj = JSON.parse(message.method.result);
          message.method.result = obj;
     }
     catch(e)
     {

     }
     $('#system_result').text("result:\n" + JSON.stringify(message.method.result, null, 4) + "\n\nerror:\n" + JSON.stringify(message.error));
};


function clearSystemLog() {
     document.getElementById("system_result").innerHTML = "";
};


function cryptoCallBack(message) {
     /*
          результат может быть как сериализованным объектом, так и просто строкой,
          если объект, то выводим в отформатированном виде
     */
     try
     {
          let obj = JSON.parse(message.method.result);
          message.method.result = obj;
     }
     catch(e)
     {

     }

     $('#crypto_result').text("result:\n" + JSON.stringify(message.method.result, null, 4) + "\n\nerror:\n" + JSON.stringify(message.error));
};


function clearCryptoLog() {
     document.getElementById("crypto_result").innerHTML = "";
};


function clearContentLog() {
     document.getElementById("message_result").innerHTML = "";
};


function clearRawLog() {
     document.getElementById("raw_result").innerHTML = "";
};


function contentCallBack(message) {
     try
     {
          let obj = JSON.parse(message.method.result);
          message.method.result = obj;
     }
     catch(e)
     {

     }
     $('#message_result').text("result:\n" + JSON.stringify(message, null, 4));

     checkModulesInstance();
};


function rawCallBack(message) {
     try
     {
          let obj = JSON.parse(message.method.result);
          message.method.result = obj;
     }
     catch(e)
     {

     }
     $('#raw_result').text("result:\n" + JSON.stringify(message, null, 4));
};


/*
     load
*/
$(document).ready(function () {
     /*
          menu actions
     */
     _menu.forEach(function (each) {
          const anchor = each.anchor;
          const links = each.links;

          links.forEach(function (each) {
               const tab = each.tab;
               const link = each.link;

               bind(link, "click", function () {
                    сhangeTab(anchor, tab);
               });
          });

     });

     /* 
          system actions
     */
     bind("system_handshake", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.handshake( systemCallBack );
     });
     bind("system_echo", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.echo( "test", systemCallBack );
     });
     bind("system_cache", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.cache( "false", systemCallBack );
     });
     bind("system_lastError", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.lastError( systemCallBack );
     });
     bind("system_config", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.config( systemCallBack );
     });
     bind("system_ipConfig", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.ipConfig(systemCallBack);
     });
     bind("system_close", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.close( systemCallBack );
     });
     bind("system_protocol", "click", function () {
          clearSystemLog();
          gosuslugiPluginSystem.protocol( systemCallBack );
     });

     /* 
          crypto actions
     */
     bind("crypto_providers", "click", function () {
          clearCryptoLog();

          let request = {};
          request.fields = {};

          gosuslugiPluginCrypto.providers(request, cryptoCallBack);
     });
     bind("crypto_providersFields", "click", function () {
          clearCryptoLog();

          let request = {};
          request.fields = {};
          request.fields.name = "Infotecs GOST 2012/512 Cryptographic Service Provider"

          gosuslugiPluginCrypto.providers(request, cryptoCallBack);
     });
     bind("crypto_certificatesAll", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = "my";
          request.fields = {};

          gosuslugiPluginCrypto.certificates(request, cryptoCallBack);
     });
     bind("crypto_certificatesToken", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = tokenStore;

          gosuslugiPluginCrypto.certificates(request, cryptoCallBack);
     });
     bind("crypto_certificatesProvider", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = providerStore;

          gosuslugiPluginCrypto.certificates(request, cryptoCallBack);
     });
     bind("crypto_certificatesCustom", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = "my";

          request.fields = {};
          request.fields.subject ='Goncharov';
          request.fields.enhancedKeyUsage = '1.3.6.1.4.1.311.10.3.4';

          gosuslugiPluginCrypto.certificates(request, cryptoCallBack);
     });
     bind("crypto_tokensAll", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = "";
          request.fields = {};

          gosuslugiPluginCrypto.tokens(request, cryptoCallBack);
     });
     bind("crypto_tokensCustom", "click", function () {
          clearCryptoLog();

          let request = {};
          request.store = tokenStore;
          request.fields = {};

          gosuslugiPluginCrypto.tokens(request, cryptoCallBack);
     });
     bind("crypto_certificateFieldsAll", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = new File([new Blob([systemCertificate.content], { type: 'text/plain' })], "certificate.txt");

          request.fields = [];

          gosuslugiPluginCrypto.certificateFields(request, cryptoCallBack);
     });
     bind("crypto_certificateFieldsCustom", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.fields = ["serialNumber", "subject"];

          gosuslugiPluginCrypto.certificateFields(request, cryptoCallBack);
     });
     bind("crypto_addCertificateWithoutContainer", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.container = "";
          request.store = "my"; 

          gosuslugiPluginCrypto.addCertificate(request, cryptoCallBack);
     });
     bind("crypto_addCertificateInContainer", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.container = "e1ff989d-f046-4e02-81a3-71586110c2ca";
          request.store = "my"; 

          gosuslugiPluginCrypto.addCertificate(request, cryptoCallBack);
     });
     bind("crypto_addCertificateInToken", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.store = tokenStore;

          gosuslugiPluginCrypto.addCertificate(request, cryptoCallBack);
     });
     bind("crypto_addCertificateAutoSearchContainer", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.container = "auto";
          request.store = "my"; 

          gosuslugiPluginCrypto.addCertificate(request, cryptoCallBack);
     });
     bind("crypto_deleteCertificate", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.store = "my";

          gosuslugiPluginCrypto.deleteCertificate(request, cryptoCallBack);
     });
     bind("crypto_deleteCertificateFromToken", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.store = tokenStore;

          gosuslugiPluginCrypto.deleteCertificate(request, cryptoCallBack);
     });
     bind("crypto_certificateRequestPcCsp", "click", function () {
          clearCryptoLog();

          let request = {};

          request.subject = "2.5.4.11=0;2.5.4.12=Тестовый директор";
          request.subjectAlternativeNames = "example.ru;example2.ru";
          request.userPrincipalName = "test@test.ru";
          request.extendedKeyUsageOids = "1.2.643.6.3.1.3.1;1.2.643.6.32.1.1;1.3.6.1.5.5.7.3.4;1.2.643.6.32.1.1.1";
          request.keyUsage = "digitalSignature;nonRepudiation;keyEncipherment;dataEncipherment;keyAgreement";

          request.attributeOids = "1.3.6.1.4.1.311.13.2.3;1.3.6.1.4.1.311.21.20;1.3.6.1.4.1.311.13.2.2";
          request.publicKeyAlgorithmParamSet = "1.2.643.2.2.35.2";

          request.validityPeriod = "";
          request.validityPeriodUnits = "100";

          request.privateKeyValidityPeriod = "Days";
          request.privateKeyValidityPeriodUnits = "400";

          request.identificationKind = "personal";

          request.provider = "Infotecs GOST 2012/512 Cryptographic Service Provider";

          gosuslugiPluginCrypto.certificateRequest(request, cryptoCallBack);
     });
     bind("crypto_certificateRequestExportable", "click", function () {
          clearCryptoLog();

          let request = {};

          request.subject = "2.5.4.11=0;2.5.4.12=Тестовый директор";
          request.subjectAlternativeNames = "example.ru;example2.ru";
          request.userPrincipalName = "test@test.ru";
          request.extendedKeyUsageOids = "1.2.643.6.3.1.3.1;1.2.643.6.32.1.1;1.3.6.1.5.5.7.3.4;1.2.643.6.32.1.1.1";
          request.keyUsage = "digitalSignature;nonRepudiation;keyEncipherment;dataEncipherment;keyAgreement";

          request.attributeOids = "1.3.6.1.4.1.311.13.2.3;1.3.6.1.4.1.311.21.20;1.3.6.1.4.1.311.13.2.2";
          request.publicKeyAlgorithmParamSet = "1.2.643.2.2.35.2";

          request.validityPeriod = "";
          request.validityPeriodUnits = "100";

          request.privateKeyValidityPeriod = "Days";
          request.privateKeyValidityPeriodUnits = "400";

          request.identificationKind = "personal";

          request.exportable = "true";

          request.provider = "Infotecs GOST 2012/512 Cryptographic Service Provider";

          gosuslugiPluginCrypto.certificateRequest(request, cryptoCallBack);
     });
     bind("crypto_certificateRequestTokenCsp", "click", function () {
          clearCryptoLog();

          let request = {};

          request.subject = "2.5.4.10=111111111122222222223333333333444444444455;2.5.4.11=1111111111222222222233;innle=1234567890;UN=test un;C=RU;2.5.4.8=77;L=asdfasdf;O=1111111111222222222233333;OU=0;CN=asdf;SN=asdfadsf;G=asdf;T=asdfasdf;STREET=adsf 88;E=00000@aaa.ru;inn=007743020560;snils=11223344595;OGRN=1027739113049";
          request.subjectAlternativeNames = "example.ru;example2.ru";
          request.userPrincipalName = "test@test.ru";
          request.extendedKeyUsageOids = "1.2.643.6.3.1.3.1;1.2.643.6.32.1.1;1.3.6.1.5.5.7.3.4;1.2.643.6.32.1.1.1";
          request.keyUsage = "digitalSignature;nonRepudiation;keyEncipherment;dataEncipherment;keyAgreement";

          request.attributeOids = "1.3.6.1.4.1.311.13.2.3;1.3.6.1.4.1.311.21.20;1.3.6.1.4.1.311.13.2.2";
          request.publicKeyAlgorithmParamSet = "1.2.643.2.2.35.2";     

          request.validityPeriod = "";
          request.validityPeriodUnits = "100";

          request.privateKeyValidityPeriod = "Days";
          request.privateKeyValidityPeriodUnits = "400";

          request.provider = tokenStore;

          gosuslugiPluginCrypto.certificateRequest(request, cryptoCallBack);
     });

     bind("crypto_certificatePolicyRequest", "click", function () {
          clearCryptoLog();

          let request = {};

          request.subject = "2.5.4.11=0;2.5.4.12=Тестовый директор";
          request.subjectAlternativeNames = "example.ru;example2.ru";
          request.userPrincipalName = "test@test.ru";
          request.extendedKeyUsageOids = "1.2.643.6.3.1.3.1;1.2.643.6.32.1.1;1.3.6.1.5.5.7.3.4;1.2.643.6.32.1.1.1";
          request.keyUsage = "digitalSignature;nonRepudiation;keyEncipherment;dataEncipherment;keyAgreement";

          request.attributeOids = "1.3.6.1.4.1.311.13.2.3;1.3.6.1.4.1.311.21.20;1.3.6.1.4.1.311.13.2.2";
          request.publicKeyAlgorithmParamSet = "1.2.643.2.2.35.2";

          request.validityPeriod = "";
          request.validityPeriodUnits = "100";

          request.privateKeyValidityPeriod = "Days";
          request.privateKeyValidityPeriodUnits = "400";

          request.certificatePolicies = "1.2.643.100.113.1;1.2.643.100.113.2";

          request.provider = "Infotecs GOST 2012/512 Cryptographic Service Provider";

          gosuslugiPluginCrypto.certificateRequest(request, cryptoCallBack);
     });
     bind("crypto_attachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ testContent ];
          request.type = "attached";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_providerAttachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.files = [ testContent ];
          request.type = "attached";
          request.provider = tokenStore;

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_tokenAttachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.files = [ testContent ];
          request.type = "attached";
          request.provider = tokenStore;

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_visualizationAttachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ testContent ];
          request.type = "attached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_visualizationAttachedSignatureGroup", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ 
            new GosuslugiPluginFileInfo( "333333333333333333333333333333", "", "", "id3", "test3.txt"),
            new GosuslugiPluginFileInfo( "2222222222"                    , "", "", "id2", "test2.txt"),
            new GosuslugiPluginFileInfo( "1"                             , "", "", "id1", "test1.txt")
          ];
          request.type = "attached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_visualizationSignatureWithoutCertificate", "click", function () {
          clearCryptoLog();

          let request = {};

          request.files = [ 
            new GosuslugiPluginFileInfo( "333333333333333333333333333333", "", "", "id3", "test3.txt"),
            new GosuslugiPluginFileInfo( "2222222222"                    , "", "", "id2", "test2.txt"),
            new GosuslugiPluginFileInfo( "1"                             , "", "", "id1", "test1.txt")
          ];
          request.type = "attached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_nonVisualizationSignatureWithoutCertificateWithCertificateResponse", "click", function () {
          clearCryptoLog();

          let request = {};

          request.files = [ 
            new GosuslugiPluginFileInfo( "333333333333333333333333333333", "", "", "id3", "test3.txt"),
            new GosuslugiPluginFileInfo( "2222222222"                    , "", "", "id2", "test2.txt"),
            new GosuslugiPluginFileInfo( "1"                             , "", "", "id1", "test1.txt")
          ];
          request.type = "attached";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_nonVisualizationSignatureWithoutCertificate", "click", function () {
          clearCryptoLog();

          let request = {};

          request.files = [ 
            new GosuslugiPluginFileInfo( "333333333333333333333333333333", "", "", "id3", "test3.txt"),
            new GosuslugiPluginFileInfo( "2222222222"                    , "", "", "id2", "test2.txt"),
            new GosuslugiPluginFileInfo( "1"                             , "", "", "id1", "test1.txt")
          ];
          request.type = "attached";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_visualizationSignatureFilesSignParams", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ 
            new GosuslugiPluginFileInfo( "", "d:/3.txt", "", "id3"),
            new GosuslugiPluginFileInfo( "", "d:/2.txt", "", "id2"),
            new GosuslugiPluginFileInfo( "", "d:/1.txt", "", "id1"),
          ];

          request.filesSignParams = [{"id":"id1","type":"detached"},{"id":"id3","type":"detached"}];
          request.type = "attached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_detachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testContent];
          request.type = "detached";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_visualizationDetachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ testContent ];
          request.type = "detached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_providerSignatureTimestampAttached", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testContent];
          request.type = "attached";

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_providerSignatureTimestampDetached", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testContent];
          request.type = "detached";

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_providerXMLSignatureTimestamp", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testXml];
          request.type = "xmldsig";

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_tokenSignatureTimestampAttached", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.files = [testContent];
          request.type = "attached";
          request.provider = tokenStore;

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_tokenSignatureTimestampDetached", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.files = [testContent];
          request.type = "detached";
          request.provider = tokenStore;

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_tokenXMLSignatureTimestamp", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = tokenCertificate;
          request.files = [testXml];
          request.type = "xmldsig";
          request.provider = tokenStore;

          request.timestamp = {};
          request.timestamp.require = "true";
          request.timestamp.tsaUrl = "http://pki3.sertum-pro.ru/tsp3/tsp.srf";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_signatureFromUrl", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ 
               new GosuslugiPluginFileInfo( "", "https://plugin-demo.iitrust.ru/static/system.js", "url", "id1"),
               new GosuslugiPluginFileInfo( "", "https://plugin-demo.iitrust.ru/static/crypto.js", "url", "id2"),
               new GosuslugiPluginFileInfo( "", "https://plugin-demo.iitrust.ru/static/1.txt",     "url", "id3"),
          ];

          request.type = "detached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_signatureJsFile", "click", function () {
          clearCryptoLog();

          const file = new File([new Blob(["Содержимое файла"], { type: 'text/plain' })], "example.txt");
          const certificate = new File([new Blob([systemCertificate.content], { type: 'text/plain' })], "certificate.txt");

          let request = {};
          request.certificate = certificate;
          request.files = [ file ];

          request.type = "detached";

          request.visualization = {};
          request.visualization.persistent = "false";

         gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_signatureWithSelectFile", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [];

          request.type = "detached";

          request.visualization = {};
          request.visualization.persistent = "false";

         gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
     });
     bind("crypto_signatureWithMix", "click", function () {
          clearCryptoLog();

          let selectFile = document.getElementById('crypto_selectFile');
          if (!selectFile) {
               selectFile = document.createElement('input');
               selectFile.setAttribute('type', 'file');
               selectFile.setAttribute('id', 'crypto_selectFile');
               selectFile.style.display = 'none';

               document.body.appendChild(selectFile);
          }
          selectFile.onchange = (event) => {

               const files = event.target.files;
               if (files.length > 0) {

                    const file = files[0];

                    let request = {};
                    request.certificate = systemCertificate;
                    request.files = [
                         new GosuslugiPluginFileInfo( "", "https://plugin-demo.iitrust.ru/static/system.js", "url", "id2"),
                         file,
                    ];

                    request.type = "detached";

                    request.visualization = {};
                    request.visualization.persistent = "false";

                    gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);

                    event.target.remove();
               }
          };
          selectFile.click(); 
     });


     bind("crypto_streamSignatureWithSelectFile", "click", function () {
          /*
               первое сообщение: данные подписи
                    meta.data.chunk: 1 / meta.data.chunks: -1 / meta.data.execute: 1 / method.data: stringify request / fileInfo.contentEncoding: temp
               последнее сообщение: данные переданы
                    meta.data.chunk = meta.data.chunks / meta.data.execute: no matter / method.data: empty
               промежуточные сообщения: данные файла
                    meta.data.chunk: chunk counter / meta.data.chunks: -1 / meta.data.execute: 1 / method.data: base64 data

               проверка:
                    перевод в потоковое подписание через хранилище с визулизаций:
                         добавить:
                              visualization: {persistent: "false"}
                         убрать:
                              message.meta.data.execute = "1"
                         обязательно для потокового подписания с / без хранилища:
                              message.meta.content.transferEncoding = "temp"
          */
          clearCryptoLog();

          let generateUuidFunc = function () {
               return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
                    var r = Math.random() * 16 | 0, v = c == "x" ? r : (r & 0x3 | 0x8);
                    return v.toString(16);
               });
          };

          let generateRequestFunc = function (fileInfos) {
               return {
                    /*
                      visualization: {persistent: "false"},
                    */
                    certificate: systemCertificate,
                    files: fileInfos,
                    type: "detached"
               };
          };

          let generateFileInfoFunc = function (file) {
               return {
                    id: generateUuidFunc(),
                    contentEncoding: "temp",
                    name: file.name,
                    content: ""
               };
          };

          let generateMessageFunc = function () {
               return new GosuslugiPluginMessage(_id, "crypto", "signatureV2");
          };

          let buffer2Base64Func = function (buffer) {
               const bytes = new Uint8Array(buffer);
               const length = bytes.byteLength;
               const chunkSize = 0x8000;
               const chunks = [];

               for (let i = 0; i < length; i += chunkSize) {
                    const chunk = bytes.subarray(i, i + chunkSize);
                    chunks.push(String.fromCharCode.apply(null, chunk));
               }

               return window.btoa(chunks.join(''));
          };

          let sendFunc = function (message) {
               window.postMessage(message, '*');
          };

          let executeFunc = async function (file) {

               let currentId = null;
               let lastError = null;
               /*
                    получение ответа
               */
               window.addEventListener("message", (event) => {
                    const message = event.data;
                    const ok = message
                         && message.id
                         && (!message.method.data?.length)
                         && (!message.meta?.data?.chunk);

                    if (ok) {
                         if (currentId == message.id && message.error && message.error.length) {
                              lastError = message.error;
                         }
                         cryptoCallBack(message);
                    }
               });

               /*
                    подготовка и отправка инициализирующего сообщения
               */
               const fileInfo = generateFileInfoFunc(file);
               const request = generateRequestFunc([fileInfo]);

               const message = generateMessageFunc();

               currentId = message.id;

               message.meta.data.chunk = "1";
               message.meta.data.chunks = "-1";
               message.meta.data.execute = "1";
               const profile = crypto.profile();
               if (profile)
                   message.meta.data.profile = profile;		

               message.method.data = JSON.stringify(request);

               await sendFunc(message);

               /*
                    подготовка и отправка данных файла
               */
               message.meta.data.id = fileInfo.id;
               message.meta.content.transferEncoding = "temp";

               const maxNmhMessageSize = 1024 * 1024;
               const chunkSize = maxNmhMessageSize >> 1;

               const reader = new FileReader();

               let position = 0;
               let chunk = 1;

               return new Promise((resolve, reject) => {
                    reader.onload = async () => {

                         try {
                              if (lastError) {
                                   throw new Error(JSON.stringify({ message: lastError }));
                              }
                              ++chunk;

                              message.meta.data.chunk = String(chunk);
                              message.method.data = buffer2Base64Func(reader.result);

                              await sendFunc(message);

                              position += chunkSize;

                              if (position < file.size) {
                                   readChunk();
                              } else {
                                   resolve(`loading file ${file.name} finished`);

                                   ++chunk;

                                   message.meta.data.chunk = String(chunk);
                                   message.meta.data.chunks = String(chunk);

                                   message.method.data = String();

                                   delete message.meta.data.id;

                                   await sendFunc(message);
                              }
                         } catch (err) {
                              reject(err);
                         }
                    };

                    reader.onerror = () => {
                         reject(reader.error);
                    }

                    function readChunk() {
                         if (lastError) return;
                         reader.readAsArrayBuffer(file.slice(position, position + chunkSize));
                    }

                    readChunk();
               });
          };

          let selectFile = document.getElementById('crypto_selectFile');
          if (!selectFile) {
               selectFile = document.createElement('input');
               selectFile.setAttribute('type', 'file');
               selectFile.setAttribute('id', 'crypto_selectFile');
               selectFile.style.display = 'none';

               document.body.appendChild(selectFile);
          }
          selectFile.onchange = (event) => {

               const files = event.target.files;
               if (files.length > 0) {

                    const file = files[0];

                    executeFunc(file);

                    event.target.remove();
               }
          };
          selectFile.click();
     });

     bind("crypto_streamSignature", "click", function () {
          clearCryptoLog();

          let generateUuidFunc = function () {
              return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
                   var r = Math.random() * 16 | 0, v = c == "x" ? r : (r & 0x3 | 0x8);
                   return v.toString(16);
              });
          };

          let generateRequestFunc = function (fileInfos) {
              return {
                   /*
                       visualization: {persistent: "false"},
                   */
                   certificate: systemCertificate,
                   files: fileInfos,
                   type: "detached"
              };
          };

          let generateFileInfoFunc = function (content) {
              return {
                   id: generateUuidFunc(),
                   contentEncoding: content ? "base64" : "temp",
                   name: "",
                   content: content || ""
              };
          };

          let buffer2Base64Func = function (buffer) {
              const bytes = new Uint8Array(buffer);
              const length = bytes.byteLength;
              const chunkSize = 0x8000;
              const chunks = [];

              for (let i = 0; i < length; i += chunkSize) {
                   const chunk = bytes.subarray(i, i + chunkSize);
                   chunks.push(String.fromCharCode.apply(null, chunk));
              }

              return window.btoa(chunks.join(''));
          };

          let sendFunc = function (request) {
              gosuslugiPluginCrypto.signatureV2(request, cryptoCallBack);
          };

          let executeFunc = async function (files) {
               /*
                    данные для отправки
               */
               const streamData = [
                   [String.raw`req1_file_1_begin_123456789_abc_абц_~!@_req1_file_1__end`, String.raw`req1_file_2_begin_123456789_abc_абц_~!@_req1_file_2_end`]
                 , [String.raw`req2_file_1`, String.raw`req2_file_2`]
               ];

               const inlineFileContent = String.raw `inline_file_content`;

               const message1Id = generateUuidFunc();
               const message2Id = generateUuidFunc();

               const req1file1Info = generateFileInfoFunc();
               const req1file2Info = generateFileInfoFunc();

               const req2file1Info = generateFileInfoFunc();
               const req2file2Info = generateFileInfoFunc();

               /*
                    для последнего файла пример "инлайнинга" - встраивание содержимого в инициирующий запрос
                    применимо для малых объемов данных
               */
               const req2file3Info = generateFileInfoFunc(buffer2Base64Func(inlineFileContent));

               let meta = {
                    data: {
                         message: "",
                         chunk:   "1",
                         chunks: "-1",
                         execute: "1"
                    }
               };

               const request1FileInfos = [req1file1Info, req1file2Info];
               const request2FileInfos = [req2file1Info, req2file2Info, req2file3Info];

               const request1 = generateRequestFunc(request1FileInfos);
               const request2 = generateRequestFunc(request2FileInfos);

               /*
                    первый запрос содержит информацию по подписания 
                    и, опционально, встроенное содержимое файла / файлов, 
                         при условии малого размера
               */
               meta.data.message = message1Id;
               await sendFunc({ ...request1, meta });

               meta.data.message = message2Id;
               await sendFunc({ ...request2, meta });

               const chunkSize = inlineFileContent.length;
               const filesInStream = streamData[0].length;
               const requestsInStream = streamData.length;

               let counter = 1;

               for (let i = 0; i < filesInStream; ++i) {

                    ++counter;

                    for (let j = 0; j < requestsInStream; ++j) {
                         const messageId = j ? message2Id           : message1Id;
                         const request   = j ? request2             : request1;
                         const fileInfo  = j ? request2FileInfos[i] : request1FileInfos[i];
                         const id = fileInfo.id;

                         const content = streamData[j][i];
                         const encoded = Base64.encode(content);

                         meta.data.message = messageId;
                         meta.data.id = id;
                         meta.data.chunk = String(counter);

                         await sendFunc({data: encoded, meta: meta});
                    }
               }

               /*
                    послединй запрос не содержит данных,
                    служит индикатором конца отправки данных
               */
               meta.data.id = String();
               meta.data.chunk = String(++counter);
               meta.data.chunks = meta.data.chunk;

               meta.data.message = message1Id;
               await sendFunc({meta: meta});

               meta.data.message = message2Id;
               await sendFunc({meta: meta});
          };

          executeFunc();
     });

     bind("crypto_streamSignatureCancelChunk", "click", function () {
          $('#crypto_result').text("waiting 5 sec ...");

          let request = {};
          request.certificate = systemCertificate;
          request.files = [ testContent ];
          request.type = "attached";

          let message = new GosuslugiPluginMessage(_id, "crypto", "signature", JSON.stringify(request));
          message.meta.data = {
               message: "",
               chunk:   "1",
               chunks: "-1",
               execute: "1",
               timeout: "5000",
          };

          _callbacks[ message.id ] = cryptoCallBack;

          window.postMessage(message, '*');
     });

     bind("crypto_cancelChunks", "click", function () {
          clearCryptoLog();
          gosuslugiPluginCrypto.cancelChunks();
     });
     bind("crypto_setChunkSize", "click", function () {
          clearCryptoLog();
          let chunkSize = gosuslugiPluginCrypto.chunkSize() << 1;
          gosuslugiPluginCrypto.chunkSize(chunkSize);
          $('#crypto_result').text("result:\n" + gosuslugiPluginCrypto.chunkSize());
     });
     bind("crypto_chunkSize", "click", function () {
          clearCryptoLog();
          $('#crypto_result').text("result:\n" + gosuslugiPluginCrypto.chunkSize());
     });
     bind("crypto_xmlGostSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testXml];
          request.type = "xmldsig";

          request.xml = {};
          request.xml.addKeyInfo = "true";
          request.xml.transforms = [uri_exc_c14n_noc, uri_envelope];

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_xmlDetachedSignature", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [testXmlDetachedSignature];
          request.type = "xmldsig";

          request.xml = {};
          request.xml.type = "detached";
          request.xml.referenceURI = "SIGNED_BY_CALLER";
          request.xml.signToElementXPath = "//*[local-name()='CallerInformationSystemSignature']";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });
     bind("crypto_xmlNormalize", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testXmlTransform;
          request.algorithm = uri_smev_transform;

          gosuslugiPluginCrypto.normalize(request, cryptoCallBack);
     });
     bind("crypto_hashGostR3411_256", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "1.2.643.7.1.1.2.2";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashGostR3411_512", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "1.2.643.7.1.1.2.3";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashAlgoFromCertificate", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.certificate = systemCertificate;

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashToken", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.certificate = tokenCertificate;
          request.provider = tokenStore;
          
          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashMd5", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "md5";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashSha1", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "sha1";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashSha256", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "sha256";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_hashSha512", "click", function () {
          clearCryptoLog();

          let request = {};
          request.file = testContent;
          request.algorithm = "sha512";

          gosuslugiPluginCrypto.hash(request, cryptoCallBack);
     });
     bind("crypto_visualization", "click", function () {
          clearCryptoLog();

          let request = {};
          request.certificate = systemCertificate;
          request.files = [];
          request.type = "attached";

          request.visualization = {};
          request.visualization.persistent = "false";

          gosuslugiPluginCrypto.signature(request, cryptoCallBack);
     });

     /*
          certificate actions
     */
     bind("delete_my_certificate", "click", function () {
          deleteCertificate(activeCertificate("my_certificates_container"));
     });
     bind("save_to_buffer_my_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("my_certificates_container"));
     });
     bind("save_to_buffer_my_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("my_certificates_container"));
     });
     bind("add_my_certificate", "click", function () {
          addCertificate(activeCertificate("my_certificates_container"));
     });
     bind("update_my_certificates", "click", function () {
          updateCertificates();
     });

     document.getElementById("find_my_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     document.getElementById("find_root_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_root_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("root_certificates_container"));
     });
     bind("save_to_buffer_root_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("root_certificates_container"));
     });
     document.getElementById("find_address_book_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_address_book_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("address_book_certificates_container"));
     });
     bind("save_to_buffer_address_book_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("address_book_certificates_container"));
     });
     document.getElementById("find_auth_root_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_auth_root_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("auth_root_certificates_container"));
     });
     bind("save_to_buffer_auth_root_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("auth_root_certificates_container"));
     });
     document.getElementById("find_certificate_authority_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_certificate_authority_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("certificate_authority_certificates_container"));
     });
     bind("save_to_buffer_certificate_authority_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("certificate_authority_certificates_container"));
     });
     document.getElementById("find_disallowed_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_disallowed_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("disallowed_certificates_container"));
     });
     bind("save_to_buffer_disallowed_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("disallowed_certificates_container"));
     });
     document.getElementById("find_trusted_people_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_trusted_people_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("trusted_people_certificates_container"));
     });
     bind("save_to_buffer_trusted_people_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("trusted_people_certificates_container"));
     });
     document.getElementById("find_trusted_publisher_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_trusted_publisher_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("trusted_publisher_certificates_container"));
     });
     bind("save_to_buffer_trusted_publisher_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("trusted_publisher_certificates_container"));
     });
     document.getElementById("find_rutoken_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_rutoken_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("rutoken_certificates_container"));
     });
     bind("save_to_buffer_rutoken_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("rutoken_certificates_container"));
     });
     document.getElementById("find_jacarta_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_jacarta_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("jacarta_certificates_container"));
     });
     bind("save_to_buffer_jacarta_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("jacarta_certificates_container"));
     });
     document.getElementById("find_esmart_certificate").addEventListener('input', function () {
          updateCertificateActions();
     });

     bind("save_to_buffer_esmart_certificate_base64", "click", function () {
          saveToBufferCertificate(activeCertificate("esmart_certificates_container"));
     });
     bind("save_to_buffer_esmart_certificate_data", "click", function () {
          saveToBufferElement(activeCertificate("esmart_certificates_container"));
     });

     /*
          providers actions
     */
     bind("update_providers", "click", function () {
          updateProviders();
     });
     document.getElementById("find_provider").addEventListener('input', function () {
          updateProviderActions();
     });

     /*
          tokens actions
     */
     bind("update_tokens", "click", function () {
          updateTokens();
     });
     document.getElementById("find_token").addEventListener('input', function () {
          updateTokenActions();
     });

     /*
          module actions
     */
     bind("update_modules", "click", function () {
          updateModules();
     });
     document.getElementById("find_module").addEventListener('input', function () {
          updateModuleActions();
     });

     /*
          content actions
     */
     bind("content_disconnect", "click", function () {
          clearContentLog();
          let message = new GosuslugiPluginMessage(_id, "system", "disconnect", "");
          window.postMessage(message, '*');
     });
     bind("content_post", "click", function () {
          clearContentLog();

          let module = document.getElementById("message_module").value;
          let method = document.getElementById("message_method").value;
          let data = document.getElementById("message_data").value;

          let message = new GosuslugiPluginMessage(_id, module, method, data);
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_debug", "click", function () {
          clearContentLog();

          let module = document.getElementById("message_module").value;
          let method = document.getElementById("message_method").value;
          let data = document.getElementById("message_data").value;

          let message = new GosuslugiPluginMessage(_id, module, method, data);
          message.meta ??= {};
          message.meta.data = {
               messageLog: "",
               messageContent: ""
          };
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_transferEncoding", "click", function () {
          clearContentLog();

          let module = document.getElementById("message_module").value;
          let method = document.getElementById("message_method").value;
          let data = document.getElementById("message_data").value;

          let message = new GosuslugiPluginMessage(_id, module, method, data);
          message.meta ??= {};
          message.meta.content.transferEncoding = "base64";
          
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_registered", "click", function () {
          clearContentLog();

          const message = new GosuslugiPluginMessage(_id, "bootstrap", "registered");
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_exists", "click", function () {
          clearContentLog();

          const message = new GosuslugiPluginMessage(_id, "bootstrap", "exists");
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_loadScripts", "click", function () {
          clearContentLog();

          const message = new GosuslugiPluginMessage(_id, "bootstrap", "loadScripts", ['system.js']);
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_loadAllScripts", "click", function () {
          clearContentLog();

          const message = new GosuslugiPluginMessage(_id, "bootstrap", "loadAllScripts");
          _callbacks[ message.id ] = contentCallBack;

          window.postMessage(message, '*');
     });
     bind("content_setPrefix", "click", function () {
          clearContentLog();

          const reset = true;
          const restore = window.crypto !== undefined && !window.crypto?.caller;
          const message = new GosuslugiPluginMessage(_id, "bootstrap", "setPrefix", {"prefix": 'testPrefix', "reset": reset});

          _callbacks[ message.id ] = function (message) {
               let result = "Обработка запроса ... ";

               if (message.error && message.error.length) {
                    result = "Ошибка сообщения: " + message.error;
               } else {
                    try {
                         testPrefix.system.caller();
                         testPrefix.crypto.caller();

                         if (reset) {
                              if (typeof system !== "undefined" && crypto?.caller) {
                                   throw "object 'system' still exists";
                              }
                              if (typeof crypto !== "undefined" && crypto?.caller) {
                                   throw "object 'crypto' still exists";
                              }
                              if (restore && !window.crypto && !window.crypto?.caller) {
                                   throw "original 'window.crypto' cannot restore";
                              }
                         }

                         result = "true";
                    } catch( e ) {
                         console.warn(`${_id}: ${(e.message || e)}`);
                         result = "Ошибка теста: " + (e.message || e);
                    }
               }
               document.getElementById("message_result").innerHTML = result;
          };

          window.postMessage(message, '*');
     });

     /*
          raw actions
     */
     bind("raw_default", "click", function () {
          clearRawLog();

          let textarea = document.getElementById("message_raw");
          textarea.value = rawDefault;
     });
     bind("raw_post", "click", function () {
          clearRawLog();

          let text = document.getElementById("message_raw").value;

          try
          {
               let message = JSON.parse( text );
               _callbacks[ message.id ] = rawCallBack;

               window.postMessage(message, '*');
          }
          catch(e)
          {
               $('#raw_result').text("debugger.error:\n" + e.message);
          }
     });

     /*
          base64 actions
     */
     bind("base64_encode", "click", function () {

          let data = document.getElementById("base64_data").value;
          let result = Base64.encode(data);

          document.getElementById("base64_result").innerHTML = result;
     });

     bind("base64_decode", "click", function () {

          let data = document.getElementById("base64_data").value;
          let result = Base64.decode(data);

          document.getElementById("base64_result").innerHTML = result;
     });

     window.addEventListener("message", (event) => {
          let message = event.data;

          if(message.node && message.node == _id) {
               if(message.mode == _ping ) {
                    let error = message.error && message.error.length;
                    if( !!error ) {
                         alert(message.error);
                    } else {
                         updateCertificates();
                         updateProviders();
                         updateTokens();
                         updateModules();

                         getModulesConfiguration();
                    }
               } else {
                    let callback = _callbacks[ message.id ];
                    if ( !!callback ) {
                         callback( message );
                         delete _callbacks[ message.id ];
                    }
               }
          }             
     });

     (async () => {
          const scripts = await bootstrap.loadScripts(['system.js', 'crypto.js']);
          console.log(`${_id}: scripts loaded ${JSON.stringify(scripts)}`);
          console.log(`${_id}: system.js loaded, caller: ${gosuslugiPluginSystem.caller()}`);
          console.log(`${_id}: crypto.js loaded, caller: ${gosuslugiPluginCrypto.caller()}`);
     })()
     .catch(error => {
         console.warn(`${_id}: failed to load scripts ${error}`);
         alert(error);
     })
     .finally(() => {
          checkModulesInstance();
          checkExtensionInstance();

          window.postMessage(new GosuslugiPluginMessage(_id, "system", "handshake", "", "", _ping), '*');

          console.log(`${_id} ready`);
     });
});
