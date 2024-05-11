<?php
include_once 'db.php';

$passenger_id = $_POST['passenger_id'];
$age = $_POST['age'];
$firstname = $_POST['firstname'];
$lastname = $_POST['lastname'];
$phone_number = $_POST['phone_number'];
$passport_id = $_POST['passport_id'];
$user_id = $_POST['user_id'];
$ticket_id = $_POST['ticket_id'];

try {
    $conn->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);


    $checkStmt = $conn->prepare("SELECT passenger_id FROM passenger WHERE passenger_id = :passenger_id");
    $checkStmt->bindParam(':passenger_id', $passenger_id);
    $checkStmt->execute();


    $ticketCheckStmt = $conn->prepare("SELECT ticket_id FROM ticket_payment WHERE ticket_id = :ticket_id");
    $ticketCheckStmt->bindParam(':ticket_id', $ticket_id);
    $ticketCheckStmt->execute();

    $userCheckStmt = $conn->prepare("SELECT user_id FROM user WHERE user_id = :user_id");
    $userCheckStmt->bindParam(':user_id', $user_id);
    $userCheckStmt->execute();


    if ($checkStmt->rowCount() > 0) {
        echo "<h2>Passenger ID already exists. Please choose a different ID.</h2>";
    } elseif ($ticketCheckStmt->rowCount() == 0) {
        echo "<h2>The provided ticket ID does not exist in the ticket_payment table.</h2>";
    } else {
        

        $stmt = $conn->prepare("INSERT INTO passenger (passenger_id, age, firstname, lastname, phone_number, passport_id, user_id, ticket_id) VALUES (:passenger_id, :age, :firstname, :lastname, :phone_number, :passport_id, :user_id, :ticket_id)");
        $stmt->bindParam(':passenger_id', $passenger_id);
        $stmt->bindParam(':age', $age);
        $stmt->bindParam(':firstname', $firstname);
        $stmt->bindParam(':lastname', $lastname);
        $stmt->bindParam(':phone_number', $phone_number);
        $stmt->bindParam(':passport_id', $passport_id);
        $stmt->bindParam(':user_id', $user_id);
        $stmt->bindParam(':ticket_id', $ticket_id);

        $stmt->execute();

        if ($stmt->rowCount() > 0) {
            echo "<h2>Passenger information added successfully!</h2>";
            echo "Passenger ID: " . $passenger_id . "<br>";
            echo "Age: " . $age . "<br>";
            echo "First Name: " . $firstname . "<br>";
            echo "Last Name: " . $lastname . "<br>";
            echo "Phone Number: " . $phone_number . "<br>";
            echo "Passport ID: " . $passport_id . "<br>";
            echo "User ID: " . $user_id . "<br>";
            echo "Ticket ID: " . $ticket_id . "<br>";
            } else {
            echo "<h2>Failed to add passenger information.</h2>";
        }
    }
} catch(PDOException $e) {
    echo "Error: " . $e->getMessage();
}

$conn = null; // Close connection
?>