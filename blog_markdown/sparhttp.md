---
title: "Building SparHTTP: An HTTP Server From Raw Sockets in C"
date: "2026-09-25"
summary: "Raw sockets, byte-stream parsing with strchr and memcpy, and the two real bugs that came from modifying a buffer while still reading from it."
pinned: false
---

# Building SparHTTP: An HTTP Server From Raw Sockets in C

## What SparHTTP Is

SparHTTP is a from-scratch HTTP server written in C. No framework, no parsing library, no `malloc`. It opens a raw TCP socket, accepts one client, reads the request off the wire, parses the request line and headers by hand, and sends back a fixed `200 OK`. It's blocking and single-connection: it handles one client, then exits.

The goal wasn't a server that could run in production. It was to see every layer between a socket and an HTTP response, the layers a framework like Flask or FastAPI normally hides. This post covers how the three files split responsibility, the two real bugs I hit while parsing and serializing, and what the server still doesn't do.

## Three Files, Three Responsibilities

- `server.c` / `server.h` — socket, bind, listen, accept
- `http.c` / `http.h` — receive, parse, build, serialize
- `main.c` — wires the two together and prints what it parsed on the way through

`server.c` has no idea what an HTTP header is. `http.c` has no idea a TCP connection exists. Neither file needs to know how the other one works, which made each piece easy to test and reason about on its own.

## Opening, Binding, and Listening

Socket setup is hardcoded, not configurable: `127.0.0.1:41783`, backlog of 10.

```c
#define PORT 41783
#define ADDRESS "127.0.0.1"
#define BACKLOG 10

int create_server() {
    int server_socket = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in server_address;
    socklen_t address_length = sizeof(server_address);

    server_address.sin_family = AF_INET;
    server_address.sin_port = htons(PORT);
    inet_pton(AF_INET, ADDRESS, &server_address.sin_addr);

    int reuse = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    if (bind(server_socket, (struct sockaddr *)&server_address, address_length) < 0)
        printf("Binding Error Occurred\n");

    if (listen(server_socket, BACKLOG) < 0)
        printf("Listen Error Occurred\n");

    return server_socket;
}
```

`SO_REUSEADDR` is there because restarting the server right after a test run made `bind()` fail, the previous socket was still sitting in `TIME_WAIT`. Without it, every restart during development meant waiting it out or changing the port.

Accepting a client is a separate function, and it returns a different socket than the one passed in:

```c
int accept_client(int server_socket) {
    struct sockaddr_in client_address;
    socklen_t address_length = sizeof(client_address);
    return accept(server_socket, (struct sockaddr *)&client_address, &address_length);
}
```

`server_socket` is the listening socket. The value `accept()` returns is the socket for that one connected client. `client_address` isn't an input, `accept()` writes into it once a connection actually lands.

## Receiving the Request

```c
ssize_t receive_request(int client_socket, char *buffer, size_t buffer_size) {
    ssize_t nread = recv(client_socket, buffer, buffer_size - 1, 0);
    if (nread < 0) {
        perror("recv");
    } else {
        buffer[nread] = '\0';
    }
    return nread;
}
```

`buffer_size - 1` leaves one byte free so the buffer can be null-terminated after the read, which is what makes it safe to run `strchr()` or `printf("%s", buffer)` on it afterward. `recv()` itself has no concept of HTTP: it doesn't know where headers end, what `Content-Length` means, or whether the request even arrived in one piece. A single call isn't guaranteed to return the whole request, a large enough request can legitimately arrive across multiple `recv()` calls. SparHTTP currently reads once and assumes it got everything, which is fine for a small request over localhost and wrong in general.

## Parsing the Request

The parser walks the buffer with one pointer instead of copying substrings around, cutting each line at `\r` and null-terminating it in place:

```c
char *current = buffer;

while (current[0] != '\r' || current[1] != '\n') {
    char *end = strchr(current, '\r');
    if (end == NULL) break;
    *end = '\0';

    if (line_count == 0) {
        char *first_space = strchr(current, ' ');
        size_t method_len = first_space - current;
        memcpy(request_line->method, current, method_len);
        request_line->method[method_len] = '\0';
        current = first_space + 1;
        // path and version get pulled out the same way, then current = end + 2
    } else {
        char *colon = strchr(current, ':');
        if (colon == NULL) break;
        size_t header_len = colon - current;
        memcpy(header[header_count].name, current, header_len);
        header[header_count].name[header_len] = '\0';
        current = colon + 2;
        // value gets copied the same way, up to `end`
    }
}
```

The first line is method, path, version, split on the two spaces. Every line after that is a header, split on the colon, until the loop hits the blank line separating headers from the body. Whatever's left in the buffer at that point becomes the body; there's no `Content-Length` check bounding it yet, it's just "whatever remains."

`strchr` returns a pointer to the character it finds, so subtracting two pointers gives the exact byte count between them, that's where `method_len = first_space - current` comes from. `memcpy` only copies bytes, it doesn't know anything about C strings, so every copied field gets a manually written `'\0'` right after.

**The bug this shape of code invites:** each line gets null-terminated in place by writing `'\0'` over the `\r`. If you then go looking for that same `\r` again with a fresh `strchr()` call instead of reusing the `end` pointer you already have, you won't find it, you already overwrote it. The result isn't a clean crash, it's a bad length calculation from subtracting against whatever `strchr` finds next, which can be well past the intended field. The fix is exactly what the code above does: compute `end` once per line, and reuse that same pointer for every length calculation on that line instead of re-deriving it.

## Building and Serializing the Response

Response construction and response serialization are two separate functions. `build_response()` decides what the response is:

```c
void build_response(Response *response) {
    strcpy(response->status, "200 OK");
    strcpy(response->body, "Connection Successful\n");
    strcpy(response->header[0].name, "Server");
    strcpy(response->header[0].value, "SparHTTP");
    response->header_count = 2; // plus Content-Type, set the same way
}
```

`serialize_response()` decides how that struct becomes actual bytes, `HTTP/1.1 200 OK\r\n...`, using `snprintf` to track how much buffer space is left after every write:

```c
int serialize_response(Response *response, char *buffer, size_t buffer_size) {
    int written = 0;
    written += snprintf(buffer + written, buffer_size - written,
        "HTTP/1.1 %s\r\n", response->status);
    // headers loop the same way, then a final snprintf appends "\r\n%s" for the body
    return written;
}
```

`main.c` never touches HTTP syntax directly, it just calls both functions in order.

**The bug that shaped `serialize_response`'s signature:** an earlier version only took `char *buffer` as a parameter and used `sizeof(buffer)` inside the function to work out how much room was left. Inside a function, `buffer` is a pointer, not the caller's array, so `sizeof(buffer)` returns the size of the pointer (8 bytes on a 64-bit system), not the actual buffer capacity. The fix is the `buffer_size` parameter above, passed explicitly by the caller, combined with `snprintf`'s return value to track exactly how much space is left after every write.

There's no `malloc` anywhere in this path. The response is small and its shape is known ahead of time, so a fixed stack buffer covers it without adding a dynamic-memory problem the project doesn't need yet.

## Why It Only Answers Once

```c
int client_socket = accept_client(create_server());
receive_request(client_socket, buffer, sizeof(buffer));
parse_request(buffer, &http_request, header, &client_msg);
build_response(&response);
send(client_socket, servers_response, serialize_response(&response, servers_response, sizeof(servers_response)), 0);
return 0;
```

That's the entire flow in `main.c`, and it runs once. There's no loop around `accept_client()`, so the process handles exactly one connection and exits. Curl it once, get a response; curl it again, "Empty reply from server," because the process is already gone. That's the current boundary, not an oversight: the request/response path needed to be solid before adding a loop and, eventually, real concurrency on top of it.

## Current Status and Known Items

SparHTTP correctly opens a socket, accepts a connection, parses a request line and headers with no external parsing library, and serializes a response back out, all with zero dynamic allocation. The following are known gaps, not bugs:

- **Single client per run.** No loop around `accept()`, the process exits after one request.
- **No routing.** Every request gets the same `200 OK`, regardless of method or path.
- **No input validation.** A malformed request (missing spaces, missing colons) isn't rejected; the parser assumes well-formed input.
- **No `Content-Length` handling.** The body is whatever bytes remain in the buffer after headers, not a length-bounded read.
- **No partial I/O handling.** Both `recv()` and `send()` are treated as single-call operations that always complete in full.

None of these are things I missed, they're the difference between a server built to learn the underlying mechanics and one built to actually serve traffic. The next version in this series moves to `epoll` or `kqueue` and a real accept loop.

---

*Code at [github.com/RichardOyelowo/sparhttp](https://github.com/RichardOyelowo/sparhttp)*
