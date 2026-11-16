Feature: Greeting visitors
  The gateway greets visitors and the counter keeps track of their visits.
  All apps run with Dapr sidecars in Kubernetes. The gateway calls the counter
  over the protocol the visitor asks for: Connect (the counter app) or gRPC
  (the counter-grpc app, the same service).

  Scenario Outline: Repeat visits are counted
    Given a new visitor
    When the visitor is greeted twice over <protocol>
    Then the second greeting reports one more visit than the first

    Examples:
      | protocol |
      | connect  |
      | grpc     |

  Scenario: Both protocols count the same visits
    Given a new visitor
    When the visitor is greeted over connect and then over grpc
    Then the second greeting reports one more visit than the first

  Scenario Outline: An invalid name is rejected
    When a visitor with an empty name is greeted over <protocol>
    Then the response status is 400
    And the error is "invalid_argument"

    Examples:
      | protocol |
      | connect  |
      | grpc     |

  Scenario Outline: A greeting is traced through the apps and their sidecars
    Given a new visitor
    When the visitor is greeted over <protocol> with a new trace id
    Then the trace contains spans of the gateway and <counter> apps and of both Dapr sidecars

    Examples:
      | protocol | counter      |
      | connect  | counter      |
      | grpc     | counter-grpc |
