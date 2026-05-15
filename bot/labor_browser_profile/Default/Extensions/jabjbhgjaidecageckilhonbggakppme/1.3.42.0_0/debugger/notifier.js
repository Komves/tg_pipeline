let notifier = ( function() {

     let messages = [];

     let _notify = function(message) {
          alert(message);
     };

     return {
          reset: function () {
               messages = [];
          },

          notify: function (message) {
               if (typeof messages[message] === 'undefined') {
                    messages[message] = message;
                    _notify(message);
               }
          },

          notifyIfError: function (message) {
               const error = message.error;
               const needNotify = error && error.length;

               if (needNotify) {
                    notify(error);
               }

               return needNotify;
          },
     };
})();