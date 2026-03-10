server = HttpServer.new()

server:setLogger(function(request, response)
  print(string.format("Method: %s, Path: %s, Status: %i", request.method, request.path, response.status))
end)

server:get("/bot/get", function(request, response)
  ...
end)

server:post("/bot/remove", function(request, response)
  is_valid_key = request:getParam("secret") == "MySecretKey";
  if is_valid_key then
    name = request:getParam("name")
    removeBot(name)
    response:setContent("You have been removed bot "..name.." successfully.", "text/plain")
  else
    response:setContent("Invalid Key.", "text/plain")
  end
end)

server:listen("0.0.0.0", 80)

-- HttpServer
server = HttpServer.new()
server:setLogger(function(request, response))
server:get(path, function(request, response))
server:post(path, function(request, response))
server:put(path, function(request, response))
server:delete(path, function(request, response))
server:patch(path, function(request, response))
server:listen("ip", port)

-- HttpRequest
request.version
request.path
request.body
request.method
request.headers
request.params
request.files
request:getHeader(key)
request:getParam(key)

-- HttpResponse
response.version
response.status
response.body
response.headers
response:setContent(content, content_type)